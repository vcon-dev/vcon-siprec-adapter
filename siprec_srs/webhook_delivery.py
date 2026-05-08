"""
Webhook delivery system for posting vCons to external endpoints.
"""

import asyncio
import hashlib
import hmac
import logging
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import aiohttp
from vcon import Vcon

from .config import WebhookConfig, WebhookEndpoint

logger = logging.getLogger(__name__)


class WebhookDelivery:
    """Handles webhook delivery of vCon files."""
    
    def __init__(self, config: WebhookConfig):
        self.config = config
        self.session: Optional[aiohttp.ClientSession] = None
        self.delivery_stats = {
            'total_attempts': 0,
            'successful_deliveries': 0,
            'failed_deliveries': 0,
            'retry_attempts': 0
        }
    
    async def start(self):
        """Start the webhook delivery system."""
        try:
            if not self.config.enabled:
                logger.info("Webhook delivery is disabled")
                return
            
            # Create HTTP session
            timeout = aiohttp.ClientTimeout(total=30)
            self.session = aiohttp.ClientSession(timeout=timeout)
            
            logger.info(f"Webhook delivery started with {len(self.config.endpoints)} endpoints")
            
        except Exception as e:
            logger.error(f"Error starting webhook delivery: {e}")
            raise
    
    async def stop(self):
        """Stop the webhook delivery system."""
        try:
            if self.session:
                await self.session.close()
                self.session = None
            
            logger.info("Webhook delivery stopped")
            
        except Exception as e:
            logger.error(f"Error stopping webhook delivery: {e}")
    
    async def deliver_vcon(self, vcon: Vcon, session_id: str,
                          call_id: str = None) -> Dict[str, Any]:
        """Deliver a vCon to all configured webhook endpoints."""
        if not self.config.enabled or not self.session:
            return {'status': 'disabled', 'endpoints': []}

        # Serialize the vCon ONCE so HMAC and HTTP body see identical bytes.
        # Stable separators avoid signature drift across Python versions.
        payload_dict = vcon.to_dict()
        body_bytes = json.dumps(
            payload_dict, separators=(',', ':'), sort_keys=True
        ).encode('utf-8')
        idempotency_key = str(vcon.uuid)

        results = {
            'session_id': session_id,
            'call_id': call_id,
            'idempotency_key': idempotency_key,
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'endpoints': [],
        }

        for endpoint in self.config.endpoints:
            try:
                result = await self._deliver_to_endpoint(
                    body_bytes, idempotency_key, endpoint, session_id, call_id
                )
                results['endpoints'].append(result)
            except Exception as e:
                logger.error(f"Error delivering to endpoint {endpoint.url}: {e}")
                results['endpoints'].append({
                    'url': endpoint.url,
                    'status': 'error',
                    'error': str(e),
                    'timestamp': datetime.now(timezone.utc).isoformat(),
                })

        # DLQ: if every endpoint failed, persist the vCon for replay.
        if results['endpoints'] and all(
            ep.get('status') != 'success' for ep in results['endpoints']
        ):
            dlq_path = self._write_to_dlq(vcon, payload_dict, results)
            if dlq_path:
                results['dlq_path'] = dlq_path

        return results

    @staticmethod
    def _sign_body(secret: str, body_bytes: bytes) -> str:
        """Return the GitHub-style `sha256=<hex>` HMAC of `body_bytes`."""
        digest = hmac.new(
            secret.encode('utf-8'), body_bytes, hashlib.sha256
        ).hexdigest()
        return f"sha256={digest}"

    def _build_headers(
        self,
        endpoint: WebhookEndpoint,
        body_bytes: bytes,
        idempotency_key: str,
        session_id: str,
        call_id: Optional[str],
    ) -> Dict[str, str]:
        headers = {
            'Content-Type': 'application/json',
            'User-Agent': 'SIPREC-SRS/1.0',
            **endpoint.headers,
            'X-Session-ID': session_id,
            'X-Delivery-Timestamp': datetime.now(timezone.utc).isoformat(),
            # Idempotency-Key lets the receiver dedupe re-deliveries (retries
            # or DLQ replays) since the vCon UUID is stable for the lifetime
            # of a recording.
            'Idempotency-Key': idempotency_key,
        }
        if call_id:
            headers['X-Call-ID'] = call_id
        if endpoint.hmac_secret:
            headers['X-Hub-Signature-256'] = self._sign_body(
                endpoint.hmac_secret, body_bytes
            )
        return headers

    async def _deliver_to_endpoint(
        self,
        body_bytes: bytes,
        idempotency_key: str,
        endpoint: WebhookEndpoint,
        session_id: str,
        call_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Deliver pre-serialized body to a specific endpoint with retries."""
        result = {
            'url': endpoint.url,
            'status': 'pending',
            'attempts': 0,
            'timestamp': datetime.now(timezone.utc).isoformat(),
        }

        headers = self._build_headers(
            endpoint, body_bytes, idempotency_key, session_id, call_id
        )

        for attempt in range(endpoint.retry_attempts + 1):
            result['attempts'] = attempt + 1
            self.delivery_stats['total_attempts'] += 1

            try:
                timeout = endpoint.timeout * (endpoint.backoff_factor ** attempt)
                async with self.session.post(
                    endpoint.url,
                    data=body_bytes,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=timeout),
                ) as response:
                    result['status_code'] = response.status
                    result['response_headers'] = dict(response.headers)

                    if 200 <= response.status < 300:
                        result['status'] = 'success'
                        result['response_body'] = await response.text()
                        self.delivery_stats['successful_deliveries'] += 1
                        logger.info(
                            f"Successfully delivered vCon to {endpoint.url} "
                            f"(attempt {attempt + 1})"
                        )
                        break

                    result['status'] = 'http_error'
                    result['response_body'] = await response.text()
                    logger.warning(
                        f"HTTP error {response.status} delivering to {endpoint.url} "
                        f"(attempt {attempt + 1}): {result['response_body']}"
                    )
                    # Don't retry on client errors (4xx).
                    if 400 <= response.status < 500:
                        break

            except asyncio.TimeoutError:
                result['status'] = 'timeout'
                logger.warning(
                    f"Timeout delivering to {endpoint.url} (attempt {attempt + 1})"
                )
            except aiohttp.ClientError as e:
                result['status'] = 'client_error'
                result['error'] = str(e)
                logger.warning(
                    f"Client error delivering to {endpoint.url} "
                    f"(attempt {attempt + 1}): {e}"
                )
            except Exception as e:
                result['status'] = 'error'
                result['error'] = str(e)
                logger.error(
                    f"Unexpected error delivering to {endpoint.url} "
                    f"(attempt {attempt + 1}): {e}"
                )

            if attempt < endpoint.retry_attempts:
                wait_time = endpoint.timeout * (endpoint.backoff_factor ** attempt)
                logger.info(f"Waiting {wait_time:.1f}s before retry...")
                await asyncio.sleep(wait_time)
                self.delivery_stats['retry_attempts'] += 1

        if result['status'] in ('pending', 'timeout', 'client_error', 'error', 'http_error'):
            result['status'] = 'failed'
            self.delivery_stats['failed_deliveries'] += 1

        result['timestamp'] = datetime.now(timezone.utc).isoformat()
        return result

    def _write_to_dlq(
        self,
        vcon: Vcon,
        payload_dict: Dict[str, Any],
        results: Dict[str, Any],
    ) -> Optional[str]:
        """Persist a failed-everywhere vCon to the dead-letter directory.

        Writes both the vCon JSON and a `.meta.json` sidecar describing
        which endpoints failed and why, so an operator can replay later.
        """
        if not self.config.dlq_path:
            return None
        try:
            dlq_dir = Path(self.config.dlq_path)
            dlq_dir.mkdir(parents=True, exist_ok=True)

            uuid = str(vcon.uuid)
            ts = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
            base = dlq_dir / f"{ts}_{uuid}"
            base.with_suffix('.vcon.json').write_text(
                json.dumps(payload_dict, separators=(',', ':'), sort_keys=True)
            )
            base.with_suffix('.meta.json').write_text(
                json.dumps(results, indent=2, default=str)
            )
            logger.error(f"Webhook delivery failed for all endpoints; wrote to DLQ: {base}")
            return str(base)
        except Exception as e:
            logger.error(f"Failed to write to DLQ: {e}")
            return None
    
    async def test_endpoint(self, endpoint: WebhookEndpoint) -> Dict[str, Any]:
        """Test a webhook endpoint with a simple ping."""
        if not self.session:
            return {'status': 'error', 'error': 'Webhook delivery not started'}
        
        test_payload = {
            'test': True,
            'timestamp': datetime.now().isoformat(),
            'message': 'SIPREC SRS webhook test'
        }
        
        headers = {
            'Content-Type': 'application/json',
            'User-Agent': 'SIPREC-SRS/1.0',
            **endpoint.headers
        }
        
        try:
            async with self.session.post(
                endpoint.url,
                json=test_payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=endpoint.timeout)
            ) as response:
                
                return {
                    'status': 'success' if response.status < 400 else 'error',
                    'status_code': response.status,
                    'response_body': await response.text(),
                    'timestamp': datetime.now().isoformat()
                }
                
        except Exception as e:
            return {
                'status': 'error',
                'error': str(e),
                'timestamp': datetime.now().isoformat()
            }
    
    def get_stats(self) -> Dict[str, Any]:
        """Get delivery statistics."""
        total_deliveries = self.delivery_stats['successful_deliveries'] + self.delivery_stats['failed_deliveries']
        success_rate = (self.delivery_stats['successful_deliveries'] / total_deliveries * 100) if total_deliveries > 0 else 0
        
        return {
            **self.delivery_stats,
            'total_deliveries': total_deliveries,
            'success_rate_percent': round(success_rate, 2),
            'endpoints_configured': len(self.config.endpoints),
            'delivery_enabled': self.config.enabled
        }
    
    def reset_stats(self):
        """Reset delivery statistics."""
        self.delivery_stats = {
            'total_attempts': 0,
            'successful_deliveries': 0,
            'failed_deliveries': 0,
            'retry_attempts': 0
        }
        logger.info("Webhook delivery statistics reset")
    
    async def deliver_batch(self, vcons: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Deliver multiple vCons in batch."""
        if not self.config.enabled or not self.session:
            return {'status': 'disabled', 'delivered': 0, 'failed': 0}
        
        results = {
            'total_vcons': len(vcons),
            'delivered': 0,
            'failed': 0,
            'timestamp': datetime.now().isoformat(),
            'results': []
        }
        
        # Deliver each vCon
        for vcon_data in vcons:
            try:
                vcon = vcon_data['vcon']
                session_id = vcon_data.get('session_id', 'unknown')
                call_id = vcon_data.get('call_id')
                
                result = await self.deliver_vcon(vcon, session_id, call_id)
                results['results'].append(result)
                
                # Count successes/failures
                all_success = all(
                    ep['status'] == 'success' 
                    for ep in result.get('endpoints', [])
                )
                
                if all_success:
                    results['delivered'] += 1
                else:
                    results['failed'] += 1
                    
            except Exception as e:
                logger.error(f"Error in batch delivery: {e}")
                results['failed'] += 1
                results['results'].append({
                    'status': 'error',
                    'error': str(e),
                    'timestamp': datetime.now().isoformat()
                })
        
        logger.info(f"Batch delivery completed: {results['delivered']} delivered, "
                   f"{results['failed']} failed")
        
        return results
    
    async def health_check(self) -> Dict[str, Any]:
        """Perform health check on all configured endpoints."""
        if not self.config.enabled:
            return {'status': 'disabled', 'endpoints': []}
        
        health_results = {
            'timestamp': datetime.now().isoformat(),
            'endpoints': []
        }
        
        # Test each endpoint
        for endpoint in self.config.endpoints:
            try:
                result = await self.test_endpoint(endpoint)
                health_results['endpoints'].append({
                    'url': endpoint.url,
                    **result
                })
            except Exception as e:
                health_results['endpoints'].append({
                    'url': endpoint.url,
                    'status': 'error',
                    'error': str(e),
                    'timestamp': datetime.now().isoformat()
                })
        
        # Overall health status
        all_healthy = all(
            ep['status'] == 'success' 
            for ep in health_results['endpoints']
        )
        health_results['status'] = 'healthy' if all_healthy else 'unhealthy'
        
        return health_results
