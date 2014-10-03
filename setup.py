from distutils.core import setup

setup(name='mwscrape',
      version='1.0',
      description='Download',
      author='Igor Tkach',
      author_email='itkach@gmail.com',
      url='http://github.com/itkach/mwscrape',
      license='MPL 2.0',
      packages=['mwscrape'],
      install_requires=['CouchDB >= 0.10', 'mwclient >= 0.7', 'futures'],
      entry_points={'console_scripts': [
          'mwscrape=mwscrape.scrape:main',
          'mwresolvec=mwscrape.resolveconflicts:main',
      ]})
