from distutils.core import setup

setup(name='mwscrape',
      version='1.0',
      description='Download',
      author='Igor Tkach',
      author_email='itkach@gmail.com',
      url='http://github.com/itkach/mwscrape',
      license='MPL 2.0',
      packages=['mwscrape'],
      #mwclient appears to need six, but doesn't declare it as dependency
      install_requires=['CouchDB >= 0.10', 'mwclient >= 0.10.0', 'pylru'],
      entry_points={'console_scripts': [
          'mwscrape=mwscrape.scrape:main',
          'mwresolvec=mwscrape.resolveconflicts:main',
      ]})
