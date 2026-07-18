#!/usr/bin/env python

from setuptools import setup

setup(
    name="tap-salesforce",
    version="1.9.0",
    description="Singer.io tap for extracting data from the Salesforce API",
    author="Stitch",
    url="https://singer.io",
    classifiers=["Programming Language :: Python :: 3 :: Only"],
    py_modules=["tap_salesforce"],
    install_requires=[
        "requests==2.32.2",
        "singer-python~=5.13",
        "xmltodict==0.11.0",
        "simple-salesforce~=1.12",
        # fix version conflicts, see https://gitlab.com/meltano/meltano/issues/193
        "idna==3.7",
        "cryptography",
        "pyOpenSSL",
    ],
    extras_require={
        # Only needed for the interactive browser (Authorization Code + PKCE)
        # auth flow. Lets the refresh-token cache use the OS keychain instead
        # of a plain file. Cron/prod installs using Client Credentials or the
        # legacy password flow never need this.
        "browser": ["keyring"],
    },
    entry_points="""
          [console_scripts]
          tap-salesforce=tap_salesforce:main
      """,
    packages=["tap_salesforce", "tap_salesforce.salesforce"],
    package_data={
        "tap_salesforce/schemas": [
            # add schema.json filenames here
        ]
    },
    include_package_data=True,
)
