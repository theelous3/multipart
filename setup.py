import sys
import os.path
from setuptools import setup

from sansio_multipart import __version__, __author__, __doc__

setup(
    name="sansio_multipart",
    version=__version__,
    description="Parser for multipart/form-data.",
    long_description=__doc__,
    author=__author__,
    author_email="theegrandmaster@gmail.com",
    url="https://github.com/theelous3/sansio-multipart-parser",
    packages=["sansio_multipart"],
    license="MIT",
    platforms="any",
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Topic :: Internet :: WWW/HTTP :: Dynamic Content :: CGI Tools/Libraries",
        "Topic :: Internet :: WWW/HTTP :: HTTP Servers",
        "Topic :: Internet :: WWW/HTTP :: WSGI",
        "Topic :: Internet :: WWW/HTTP :: WSGI :: Application",
        "Topic :: Internet :: WWW/HTTP :: WSGI :: Middleware",
        "Topic :: Internet :: WWW/HTTP :: WSGI :: Server",
        "Programming Language :: Python :: 3",
    ],
)
