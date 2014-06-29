from setuptools import setup

setup(
    name='olleh',
    author='Choongmin Lee',
    author_email='choongmin@me.com',
    version='0.1.1',
    description='A command line tool to fetch usage data from olleh.com.',
    py_modules=['olleh'],
    install_requires=[
        'click==2.2',
        'requests==2.3.0',
    ],
    entry_points={
        'console_scripts': [
            'olleh=olleh:main',
        ],
    }
)
