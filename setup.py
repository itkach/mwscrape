from distutils.core import setup

import os
import sys

if 'publish' in sys.argv:
    os.system('python setup.py sdist upload')
    sys.exit()


setup(
		name='mwscrape',
		version='1.1',
		description='Download',
		author='Igor Tkach',
		author_email='itkach@gmail.com',
		url='http://github.com/itkach/mwscrape',
		license='MPL 2.0',
		
		#packages=['mwscrape'], # don't have to have this to create a package
		#package_dir={'': '..'},
		
		#mwclient appears to need six, but doesn't declare it as dependency
		install_requires=['futures', 'CouchDB >= 0.10', 'mwclient >= 0.7.2', 'pylru'],
		entry_points={'console_scripts': [
			'mwscrape=mwscrape:main',
			'mwresolvec=resolveconflicts:main',
		]},
		
		classifiers=[
			'Development Status :: 5 - Production/Stable',
			
			'Intended Audience :: Developers',
			'Intended Audience :: SEO specialists',
			'Intended Audience :: Information Technology',
			'Intended Audience :: Science/Research',
			
			'License :: OSI Approved :: Mozilla Public License 2.0 (MPL 2.0)',
			'Operating System :: OS Independent',
			
			'Programming Language :: Python',
			'Programming Language :: Python :: 3',
			'Programming Language :: Python :: 3.7',
			'Topic :: Internet :: WWW/HTTP :: Indexing/Search',
		],
)