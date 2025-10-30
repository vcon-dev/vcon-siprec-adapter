"""
Webhook delivery system for posting vCons to external endpoints.
"""

import asyncio
import logging
import json
from typing import Dict, Any, Optional, List
from datetime import datetime
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
        
        results = {
            'session_id': session_id,
            'call_id': call_id,
            'timestamp': datetime.now().isoformat(),
            'endpoints': []
        }
        
        # Deliver to each endpoint
        for endpoint in self.config.endpoints:
            try:
                result = await self._deliver_to_endpoint(vcon, endpoint, session_id, call_id)
                results['endpoints'].append(result)
                
            except Exception as e:
                logger.error(f"Error delivering to endpoint {endpoint.url}: {e}")
                results['endpoints'].append({
                    'url': endpoint.url,
                    'status': 'error',
                    'error': str(e),
                    'timestamp': datetime.now().isoformat()
                })
        
        return results
    
    async def _deliver_to_endpoint(self, vcon: Vcon, endpoint: WebhookEndpoint, 
                                 session_id: str, call_id: str = None) -> Dict[str, Any]:
        """Deliver vCon to a specific endpoint with retry logic."""
        result = {
            'url': endpoint.url,
            'status': 'pending',
            'attempts': 0,
            'timestamp': datetime.now().isoformat()
        }
        
        # Prepare headers
        headers = {
            'Content-Type': 'application/json',
            'User-Agent': 'SIPREC-SRS/1.0',
            **endpoint.headers
        }
        
        # Add custom headers for vCon delivery
        headers['X-Session-ID'] = session_id
        if call_id:
            headers['X-Call-ID'] = call_id
        headers['X-Delivery-Timestamp'] = datetime.now().isoformat()
        
        # Prepare payload
        payload = vcon.to_dict()
        
        # Retry logic
        for attempt in range(endpoint.retry_attempts + 1):
            result['attempts'] = attempt + 1
            self.delivery_stats['total_attempts'] += 1
            
            try:
                # Calculate timeout with backoff
                timeout = endpoint.timeout * (endpoint.backoff_factor ** attempt)
                
                # Make HTTP request
                async with self.session.post(
                    endpoint.url,
                    json=payload,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=timeout)
                ) as response:
                    
                    result['status_code'] = response.status
                    result['response_headers'] = dict(response.headers)
                    
                    if response.status >= 200 and response.status < 300:
                        # Success
                        result['status'] = 'success'
                        result['response_body'] = await response.text()
                        self.delivery_stats['successful_deliveries'] += 1
                        
                        logger.info(f"Successfully delivered vCon to {endpoint.url} "
                                  f"(attempt {attempt + 1})")
                        break
                    
                    else:
                        # HTTP error
                        result['status'] = 'http_error'
                        result['response_body'] = await response.text()
                        
                        logger.warning(f"HTTP error {response.status} delivering to {endpoint.url} "
                                     f"(attempt {attempt + 1}): {result['response_body']}")
                        
                        # Don't retry on client errors (4xx)
                        if 400 <= response.status < 500:
                            break
                    
            except asyncio.TimeoutError:
                result['status'] = 'timeout'
                logger.warning(f"Timeout delivering to {endpoint.url} (attempt {attempt + 1})")
                
            except aiohttp.ClientError as e:
                result['status'] = 'client_error'
                result['error'] = str(e)
                logger.warning(f"Client error delivering to {endpoint.url} "
                             f"(attempt {attempt + 1}): {e}")
                
            except Exception as e:
                result['status'] = 'error'
                result['error'] = str(e)
                logger.error(f"Unexpected error delivering to {endpoint.url} "
                           f"(attempt {attempt + 1}): {e}")
            
            # Wait before retry (exponential backoff)
            if attempt < endpoint.retry_attempts:
                wait_time = endpoint.timeout * (endpoint.backoff_factor ** attempt)
                logger.info(f"Waiting {wait_time:.1f}s before retry...")
                await asyncio.sleep(wait_time)
                self.delivery_stats['retry_attempts'] += 1
        
        # Mark as failed if all attempts failed
        if result['status'] in ['pending', 'timeout', 'client_error', 'error', 'http_error']:
            result['status'] = 'failed'
            self.delivery_stats['failed_deliveries'] += 1
        
        result['timestamp'] = datetime.now().isoformat()
        return result
    
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
