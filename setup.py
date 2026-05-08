#!/usr/bin/env python3
"""
Setup script for SIPREC SRS to vCon Server.
"""

from setuptools import setup, find_packages
from pathlib import Path

# Read the README file
readme_file = Path(__file__).parent / "README.md"
long_description = readme_file.read_text(encoding="utf-8") if readme_file.exists() else ""

# Read requirements
requirements_file = Path(__file__).parent / "requirements.txt"
requirements = []
if requirements_file.exists():
    requirements = requirements_file.read_text().strip().split('\n')
    requirements = [req.strip() for req in requirements if req.strip() and not req.startswith('#')]

setup(
    name="siprec-srs-vcon",
    version="1.0.0",
    description="SIPREC Session Recording Server that converts SIP conversations to spec-compliant vCon (draft-ietf-vcon-vcon-core-02) format",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/vcon-dev/vcon-siprec-adapter",
    license="MIT",
    packages=find_packages(),
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Topic :: Communications :: Telephony",
        "Topic :: Software Development :: Libraries :: Python Modules",
    ],
    python_requires=">=3.8",
    install_requires=requirements,
    extras_require={
        "dev": [
            "pytest>=7.0.0",
            "pytest-asyncio>=0.21.0",
            "pytest-mock>=3.10.0",
            "black>=23.0.0",
            "flake8>=6.0.0",
            "mypy>=1.0.0",
        ],
    },
    entry_points={
        # main.py lives at the repo root, not inside the siprec_srs/
        # package, and exposes an async `main()` that needs asyncio.run.
        # The `siprec-srs` console script wraps that for shell users.
        "console_scripts": [
            "siprec-srs=siprec_srs.cli:run",
        ],
    },
    include_package_data=True,
    zip_safe=False,
)
