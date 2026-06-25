from setuptools import setup

package_name = 'openflex_gui'

setup(
    name=package_name,
    version='1.0.0',
    packages=[package_name],
    data_files=[
        ('share/ament_index/resource_index/packages', ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
    ],
    package_data={package_name: ['*.sh', '*.svg']},
    install_requires=['setuptools'],
    zip_safe=True,
    entry_points={
        'console_scripts': [
            'openflex_gui = openflex_gui.main_window:main',
        ],
    },
)
