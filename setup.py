from setuptools import setup, find_packages

setup(
    name='olleh',
    version='0.1',
    packages=find_packages(),
    include_package_data=True,
    install_requires=[
        'click==0.1',
        'requests==2.2.1',
    ],
    entry_points={
        'console_scripts': [
            'olleh=olleh:main',
        ],
    }
)
