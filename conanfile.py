#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import shutil
import re

from conans import tools
from conans import ConanFile
from conans import CMake

from fnmatch import fnmatch
from pathlib import Path
from functools import update_wrapper

try:
    from cStringIO import StringIO
except ImportError:
    from io import StringIO





# Utilities
class LazyProperty(property):
    def __init__(self, method, fget=None, fset=None, fdel=None, doc=None):

        self.method = method
        self.cache_name = "_{}".format(self.method.__name__)

        doc = doc or method.__doc__
        super(LazyProperty, self).__init__(fget=fget, fset=fset, fdel=fdel, doc=doc)

        update_wrapper(self, method)

    def __get__(self, instance, owner):

        if instance is None:
            return self

        if hasattr(instance, self.cache_name):
            result = getattr(instance, self.cache_name)
        else:
            if self.fget is not None:
                result = self.fget(instance)
            else:
                result = self.method(instance)

            setattr(instance, self.cache_name, result)

        return result





# global module utility functions

#
# CMAKE Default Config
#
def get_c_flags(**kwargs):
    if kwargs.get('is_posix', tools.os_info.is_posix):
        if kwargs.get('is_macos', tools.os_info.is_macos):
            # Our old macos CI is done on a old E5620 Intel(R) Xeon(R) CPU, which doesn't support AVX and f16c
            # CPU with 64-bit extensions, MMX, SSE, SSE2, SSE3, SSSE3, SSE4.1, SSE4.2,
            # POPCNT, AES and PCLMUL instruction set support.
            flags = '-march=westmere'
            flags += ' -mtune=intel'
            flags += ' -mfpmath=sse'
            flags += ' -arch x86_64'
            flags += ' -mmacosx-version-min=10.14'
            flags += ' -DGL_SILENCE_DEPRECATION'
            return flags
        else:
            # CPU with 64-bit extensions, MMX, SSE, SSE2, SSE3, SSSE3, SSE4.1, SSE4.2,
            # POPCNT, AVX, AES, PCLMUL, FSGSBASE instruction set support.
            flags = '-march=sandybridge'
            flags += ' -mtune=generic'
            flags += ' -mfpmath=sse'
            return flags
    else:
        # Windows flags..
        flags = '/favor:blend'
        flags += ' /fp:precise'
        flags += ' /Qfast_transcendentals'
        flags += ' /arch:AVX'
        flags += ' /MP'
        flags += ' /bigobj'
        flags += ' /EHsc'
        flags += ' /D_ENABLE_EXTENDED_ALIGNED_STORAGE'
        return flags


def get_cxx_flags(**kwargs):
    return get_c_flags(**kwargs)


def get_release_c_flags(**kwargs):
    if kwargs.get('is_posix', tools.os_info.is_posix):
        return '-O3 -fomit-frame-pointer -DNDEBUG'
    elif kwargs.get('is_windows', tools.os_info.is_windows):
        return '/O2 /Ob2 /MD /DNDEBUG'
    else:
        return ''


def get_release_cxx_flags(**kwargs):
    return get_release_c_flags(**kwargs)


def get_debug_c_flags(**kwargs):
    if kwargs.get('is_posix', tools.os_info.is_posix):
        return '-Og -g -D_DEBUG'
    elif kwargs.get('is_windows', tools.os_info.is_windows):
        return '/Ox /Oy- /Ob1 /Z7 /MDd /D_DEBUG'
    else:
        return ''


def get_debug_cxx_flags(**kwargs):
    return get_debug_c_flags(**kwargs)


def get_relwithdebinfo_c_flags(**kwargs):
    if kwargs.get('is_posix', tools.os_info.is_posix):
        return '-O3 -g -DNDEBUG'
    elif kwargs.get('is_windows', tools.os_info.is_windows):
        return get_release_c_flags(**kwargs) + ' /Z7'
    else:
        return ''


def get_relwithdebinfo_cxx_flags(**kwargs):
    return get_relwithdebinfo_c_flags(**kwargs)


def get_thorough_debug_c_flags(**kwargs):
    if kwargs.get('is_posix', tools.os_info.is_posix):
        return '-O0 -g3 -D_DEBUG'
    elif kwargs.get('is_windows', tools.os_info.is_windows):
        return '/Od /Ob0 /RTC1 /sdl /Z7 /MDd /D_DEBUG'
    else:
        return ''


def get_thorough_debug_cxx_flags(**kwargs):
    return get_thorough_debug_c_flags(**kwargs)


def get_full_c_flags(**kwargs):
    c_flags = get_c_flags(**kwargs)
    build_type = str(kwargs.get('build_type', 'debug')).lower()

    if build_type == 'debug':
        c_flags += ' ' + get_debug_c_flags(**kwargs)
    elif build_type == 'release':
        c_flags += ' ' + get_release_c_flags(**kwargs)
    elif build_type == 'relwithdebinfo':
        c_flags += ' ' + get_relwithdebinfo_c_flags(**kwargs)

    return c_flags


def get_full_cxx_flags(**kwargs):
    return get_full_c_flags(**kwargs)

#
# CMake project wrapper
#
def generate_cmake_wrapper(**kwargs):
    # Get the cmake wrapper path
    cmakelists_path = kwargs.get('cmakelists_path', 'CMakeLists.txt')
    cmakelists_exists = Path(cmakelists_path).is_file()

    # If there is an existing CMakeLists.txt, because of some strange package like libsgm, we must rename it
    if cmakelists_exists:
        shutil.move(cmakelists_path, cmakelists_path + '.upstream')

    # Write the file content
    with open(cmakelists_path, 'w') as cmake_wrapper:
        cmake_wrapper.write('cmake_minimum_required(VERSION 3.15)\n')

        # New policies management. It must be done before 'project(cmake_wrapper)'
        new_policies = kwargs.get('new_policies', None)
        if new_policies:
            for new_policy in new_policies:
                cmake_wrapper.write("cmake_policy(SET {0} NEW)\n".format(new_policy))

        # Old policies management. It must be done before 'project(cmake_wrapper)'
        old_policies = kwargs.get('old_policies', None)
        if old_policies:
            for old_policy in old_policies:
                cmake_wrapper.write("cmake_policy(SET {0} OLD)\n".format(old_policy))

        cmake_wrapper.write('project(cmake_wrapper)\n')
        cmake_wrapper.write(
            'if(EXISTS "${CMAKE_BINARY_DIR}/conanbuildinfo.cmake")\n'
        )
        cmake_wrapper.write(
            '   include(${CMAKE_BINARY_DIR}/conanbuildinfo.cmake)\n'
        )
        cmake_wrapper.write(
            'elseif(EXISTS "${CMAKE_BINARY_DIR}/../conanbuildinfo.cmake")\n'
        )
        cmake_wrapper.write(
            '   include(${CMAKE_BINARY_DIR}/../conanbuildinfo.cmake)\n'
        )
        cmake_wrapper.write(
            'elseif(EXISTS "${CMAKE_BINARY_DIR}/../../conanbuildinfo.cmake")\n'
        )
        cmake_wrapper.write(
            '   include(${CMAKE_BINARY_DIR}/../../conanbuildinfo.cmake)\n'
        )
        cmake_wrapper.write(
            'elseif(EXISTS "${CMAKE_BINARY_DIR}/../../../conanbuildinfo.cmake")\n'
        )
        cmake_wrapper.write(
            '   include(${CMAKE_BINARY_DIR}/../../../conanbuildinfo.cmake)\n'
        )
        cmake_wrapper.write('endif()\n')
        cmake_wrapper.write('conan_basic_setup()\n')

        # Add common flags
        cmake_wrapper.write(
            'add_compile_options(' + get_cxx_flags() + ')\n'
        )

        # Disable warnings and error because of warnings
        cmake_wrapper.write(
            'add_compile_options("$<$<CXX_COMPILER_ID:MSVC>:/W0;/WX->")\n'
        )

        cmake_wrapper.write(
            'add_compile_options("$<$<CXX_COMPILER_ID:GNU,Clang,AppleClang>:-w;-Wno-error>")\n'
        )

        # Get build type, defaulting to debug
        build_type = str(kwargs.get('build_type', 'debug')).lower()

        if build_type == 'release':
            # Add release flags
            cmake_wrapper.write(
                'add_compile_options(' + get_release_cxx_flags() + ')\n'
            )
        elif build_type == 'debug':
            # Add debug flags
            debug_flags = get_debug_cxx_flags()
            cmake_wrapper.write(
                'add_compile_options(' + debug_flags + ')\n'
            )

            # Special case on windows, which doesn't support mixing /Ox with /RTC1
            if tools.os_info.is_windows and (
                '/O1' in debug_flags or '/O2' in debug_flags or '/Ox' in debug_flags
            ):
                cmake_wrapper.write(
                    'string(REGEX REPLACE "/RTC[1csu]+" "" CMAKE_C_FLAGS_DEBUG "${CMAKE_C_FLAGS_DEBUG}")\n'
                )
                cmake_wrapper.write(
                    'string(REGEX REPLACE "/RTC[1csu]+" "" CMAKE_CXX_FLAGS_DEBUG "${CMAKE_CXX_FLAGS_DEBUG}")\n'
                )
        elif build_type == 'relwithdebinfo':
            # Add relwithdebinfo flags
            cmake_wrapper.write(
                'add_compile_options(' + get_relwithdebinfo_cxx_flags() + ')\n'
            )

        # Write CUDA specific code
        setup_cuda = kwargs.get('setup_cuda', False)

        if setup_cuda:
            cmake_wrapper.write(
                'find_package(CUDA)\n'
            )

            cmake_wrapper.write(
                'CUDA_SELECT_NVCC_ARCH_FLAGS(ARCH_FLAGS ' + ' '.join(get_cuda_arch()) + ')\n'
            )

            cmake_wrapper.write(
                'LIST(APPEND CUDA_NVCC_FLAGS ${ARCH_FLAGS})\n'
            )

            # Propagate host CXX flags
            host_cxx_flags = ",\\\""
            host_cxx_flags += get_full_cxx_flags(build_type=build_type).replace(' ', "\\\",\\\"")
            host_cxx_flags += "\\\""

            cmake_wrapper.write(
                'LIST(APPEND CUDA_NVCC_FLAGS -Xcompiler ' + host_cxx_flags + ')\n'
            )

        # Write additional options
        additional_options = kwargs.get('additional_options', None)
        if additional_options:
            cmake_wrapper.write(additional_options + '\n')

        # Write the original subdirectory / include
        if cmakelists_exists:
            cmake_wrapper.write('include("CMakeLists.txt.upstream")\n')
        else:
            source_subfolder = kwargs.get(
                'source_subfolder', 'source_subfolder'
            )
            cmake_wrapper.write(
                'add_subdirectory("' + source_subfolder + '")\n'
            )



#
# CUDA Defaults
#
def get_cuda_version():
    return ['9.2', '10.0', '10.1', '10.2', '11.0', '11.1', '11.2', '11.4', '11.5', '11.6', '11.7', 'None']


def get_cuda_arch():
    return ['5.0', '5.2', '6.0', '6.1', '7.0', '7.2', '7.5', '8.0', '8.6', '8.7', '9.0']



#
# Conan fixes
#
def __fix_conan_dependency_path(conanfile, file_path, package_name):
    try:
        tools.replace_in_file(
            file_path,
            conanfile.deps_cpp_info[package_name].rootpath.replace('\\', '/'),
            "${CONAN_" + package_name.upper() + "_ROOT}",
            strict=False
        )
    except Exception:
        conanfile.output.info("Ignoring {0}...".format(package_name))


def __cmake_fix_macos_sdk_path(conanfile, file_path):
    try:
        # Read in the file
        with open(file_path, 'r') as file:
            file_data = file.read()

        if file_data:
            # Replace the target string
            pattern = (r';/Applications/Xcode\.app/Contents/Developer'
                       r'/Platforms/MacOSX\.platform/Developer/SDKs/MacOSX\d\d\.\d\d\.sdk/usr/include')

            # Match sdk path
            file_data = re.sub(pattern, '', file_data, re.M)

            # Write the file out again
            with open(file_path, 'w') as file:
                file.write(file_data)

    except Exception:
        conanfile.output.info(
            "Skipping macOS SDK fix on {0}...".format(file_path)
        )


def fix_conan_path(
    conanfile,
    root,
    wildcard,
    build_folder=None
):
    # Normalization
    package_folder = conanfile.package_folder.replace('\\', '/')

    if build_folder:
        build_folder = build_folder.replace('\\', '/')

    conan_root = '${CONAN_' + conanfile.name.upper() + '_ROOT}'

    # Recursive walk
    for path, subdirs, names in os.walk(root):
        for name in names:
            if fnmatch(name, wildcard):
                wildcard_file = os.path.join(path, name)

                # Fix package_folder paths
                tools.replace_in_file(
                    wildcard_file, package_folder, conan_root, strict=False
                )

                # Fix build folder paths
                if build_folder:
                    tools.replace_in_file(
                        wildcard_file, build_folder, conan_root, strict=False
                    )

                # Fix specific macOS SDK paths
                if tools.os_info.is_macos:
                    __cmake_fix_macos_sdk_path(
                        conanfile, wildcard_file
                    )

                # Fix dependencies paths
                for requirement in conanfile.requires:
                    __fix_conan_dependency_path(
                        conanfile, wildcard_file, requirement
                    )



#
# Baseclass for CUDA Dependency
#

## somehow exporting does not provide access to package options
# so defining cuda_root only works with conan create, but not conan export ..
CUDA_ROOT_DEFAULT = None
if tools.os_info.is_windows:
    CUDA_ROOT_DEFAULT = "C:\\Program Files\\NVIDIA GPU Computing Toolkit\\CUDA\\v11.1"
elif tools.os_info.is_linux:
    CUDA_ROOT_DEFAULT = "/usr/local/cuda-11.1"

# reusable code for ConanFile
class CampCudaBase(object):
    """
    BaseClass for Conan PythonRequires for packages that have a cuda dependency.

    only use these methods in one of:  validate(), build(), package(), package_info()

    Expects two options: 
    'cuda_version': requested CUDA Version
    'cuda_root': path to cuda sdk root directory
    """

    @LazyProperty
    def _cuda_sdk_root(self):
        cuda_version = str(self.options.get_safe("cuda_version", "ANY"))
        cuda_root = str(self.options.get_safe("cuda_root", "ANY"))
        if cuda_root == "ANY":
            cuda_root = None
        if cuda_version == "ANY":
            cuda_version = None
        
        if cuda_root is None:
            cuda_root, cv = self.__cuda_get_sdk_root_and_version(cuda_version)
            if cuda_version is not None and cv != cuda_version:
                raise ValueError("CUDA SDK Version requested != found ({0} vs {1})".format(cuda_version, cv))
            if cuda_version is None:
                cuda_version = cv
            if cuda_root is None:
                cuda_root = CUDA_ROOT_DEFAULT
        

        if cuda_version is not None:
            if not self.__cuda_check_sdk_version(cuda_root, cuda_version):
                raise RuntimeError("No suitable CUDA SDK Root directory found - cuda_check_sdk_version failed.")

        return cuda_root


    @LazyProperty
    def _cuda_version(self):
        cuda_root = self._cuda_sdk_root
        if cuda_root is None:
            return None
        return self.__cuda_get_sdk_version(cuda_root)

    @LazyProperty
    def _cuda_bin_dir(self):
        return os.path.join(self._cuda_sdk_root, "bin")


    @LazyProperty
    def _cuda_lib_dir(self):
        return os.path.join(self._cuda_sdk_root, "lib")


    @LazyProperty
    def _cuda_include_dir(self):
        return os.path.join(self._cuda_sdk_root, "include")

    # internal methods

    def __cuda_get_sdk_root_and_version(self, cuda_version=None):
        cuda_sdk_root = None
        cuda_version_found = None

        supported_versions = reversed([v for v in get_cuda_version() if v != 'None'])
        find_cuda_versions = []
        if cuda_version is not None:
            if cuda_version not in supported_versions:
                raise ValueError("Unsupported CUDA SDK Version requested: {0}".format(cuda_version))
            find_cuda_versions = [cuda_version,]
        else:
            find_cuda_versions = supported_versions


        if tools.os_info.is_linux:
            for cv in find_cuda_versions:
                cuda_sdk_root = "/usr/local/cuda-{0}".format(cv)
                if os.path.exists(cuda_sdk_root):
                    cuda_sdk_root = cuda_sdk_root
                    cuda_version_found = cv
                    break
        if tools.os_info.is_windows:
            default_path = "C:\\Program Files\\NVIDIA GPU Computing Toolkit\\CUDA\\v{}"
            for version in find_cuda_versions:
                cuda_sdk_root = default_path.format(version)
                if os.path.exists(cuda_sdk_root):
                    cuda_sdk_root = cuda_sdk_root
                    cuda_version_found = version
                    break
        if cuda_sdk_root is None or cuda_version_found is None:
            raise ValueError("Could not find CUDA Sdk version: {0}".format(cuda_version or "ANY"))

        self.output.info("Found CUDA SDK {0} at: {1}".format(cuda_version_found, cuda_sdk_root))
        return cuda_sdk_root, cuda_version_found


    def __cuda_get_nvcc_filename(self, cuda_sdk_root):
        return os.path.join(cuda_sdk_root, 'bin', 'nvcc')


    def __cuda_run_nvcc_command(self, cuda_sdk_root, command):
        if cuda_sdk_root is None:
            raise ValueError("Invalid CUDA SDK root: None")
        nvcc_executable = self.__cuda_get_nvcc_filename(cuda_sdk_root)
        output = StringIO()
        self.output.info('running command: "{0}" {1}'.format(nvcc_executable, command))
        self.run('"{0}" {1}'.format(nvcc_executable, command), output=output, run_environment=True)
        result = output.getvalue().strip()
        return result if result and result != "" else None



    def __cuda_get_sdk_version(self, cuda_sdk_root):
        cmd = "--version"
        result = self.__cuda_run_nvcc_command(cuda_sdk_root, cmd)
        match = re.match( r"^.*\, release\s(\S+)\,.*$", result.splitlines()[3])
        success = True
        if match:
            version = match.groups()[0]
            self.output.info("Found CUDA SDK Version {0}".format(version))
            return version
        return None


    def __cuda_check_sdk_version(self, cuda_sdk_root, cuda_version):
        if cuda_sdk_root is None:
            raise ValueError("Invalid CUDA SDK root: None")
        version = self.__cuda_get_sdk_version(cuda_sdk_root)
        if version != cuda_version:
            self.output.error("Invalid CUDA SDK version found: {0} expected: {1}".format(version, cuda_version))
            return False
        return True



#
# Python configuration
#


# reusable code for ConanFile
class CampPythonBase(object):
    """
    BaseClass for Conan PythonRequires for packages that have a python dependency.

    only use these methods in one of:  validate(), build(), package(), package_info()
    
    Expects two options: 
    'python': name and or path to python interpreter
    'with_system_python': flag to state if system python should be used
    """


    @LazyProperty
    def _python_exec(self):
        cmd = None
        with_system_python = True
        if 'python' in self.options:
            cmd = str(self.options.python)
        if 'with_system_python' in self.options:
            with_system_python = bool(self.options.with_system_python)
        return self.__python_get_interpreter_fullpath(cmd, with_system_python)

    @LazyProperty
    def _python_version(self):
        return self.__python_get_version(self._python_exec)

    @LazyProperty
    def _python_version_nodot(self):
        return self.__python_get_version_nodot(self._python_exec)

    @LazyProperty
    def _python_lib(self):
        py_lib = None
        if tools.os_info.is_windows and not tools.os_info.detect_windows_subsystem():
            py_lib = self._python_stdlib
            if py_lib:
                py_lib = os.path.join(os.path.dirname(py_lib), "libs", "python" + self._python_version_nodot + ".lib")
        elif tools.os_info.is_macos:
            py_lib = os.path.join(self.__python_get_sysconfig_var('LIBDIR'), self.__python_get_sysconfig_var('LIBRARY'))
        else:
            py_lib = os.path.join(self.__python_get_sysconfig_var('LIBDIR'), self.__python_get_sysconfig_var('LDLIBRARY'))
        return py_lib

    @LazyProperty
    def _python_lib_ldname(self):
        py_lib_ldname = None
        if tools.os_info.is_windows and not tools.os_info.detect_windows_subsystem():
            py_lib_ldname = os.path.basename(self._python_lib)
        else:
            py_lib_ldname = re.sub(r'lib', '', os.path.splitext(os.path.basename(self._python_lib))[0])
        return py_lib_ldname

    @LazyProperty
    def _python_stdlib(self):
        return self.__python_get_sysconfig_path("stdlib")

    @LazyProperty
    def _python_prefix(self):
        return self.__python_get_sysconfig_var("prefix")

    @LazyProperty
    def _python_bindir(self):
        return self.__python_get_sysconfig_var("BINDIR")

    @LazyProperty
    def _python_include_dir(self):
        for py_include in [self.__python_get_sysconfig_path("include"), self.__python_get_sysconfig_var('INCLUDEPY')]:
                if os.path.exists(os.path.join(py_include, 'pyconfig.h')):
                    return py_include
        return None


    #internal functions
    def __python_run_command(self, python_exec, command):
        output = StringIO()
        self.output.info('running python command: "{0}" -c "{1}"'.format(python_exec, command))
        self.run('"{0}" -c "{1}"'.format(python_exec, command), output=output, run_environment=True)
        return output.getvalue().strip()


    def __python_get_interpreter_fullpath(self, command=None, use_system_python=True):
        if command is None and not use_system_python:
            raise ValueError("Python interpreter not found - if use_system_python=False, you must specify a command")
        if command is None:
            if tools.os_info.is_windows and not tools.os_info.detect_windows_subsystem():
                command = "python"
            else:
                command = "python3"

        try:
            return self.__python_run_command(command, "import sys; print(sys.executable)")
        except Exception as e:
            self.output.error("Error while running python command: {0}".format(e))
            raise RuntimeError("Error while executing python command.")


    def __python_get_sysconfig_var(self, var_name):
        try:
            cmd = "import sysconfig; print(sysconfig.get_config_var('{0}'))".format(var_name)
            return self.__python_run_command(self._python_exec, cmd)
        except Exception as e:
            self.output.error("Error while running python command: {0}".format(e))
            raise RuntimeError("Error while executing python command.")


    def __python_get_sysconfig_path(self, path_name):
        try:
            cmd = "import sysconfig; print(sysconfig.get_path('{0}'))".format(path_name)
            return self.__python_run_command(self._python_exec, cmd)
        except Exception as e:
            self.output.error("Error while running python command: {0}".format(e))
            raise RuntimeError("Error while executing python command.")

    def __python_get_version(self, python_exec):
        try:
            cmd = "from sys import *; print('{0}.{1}'.format(version_info[0],version_info[1]))"
            return self.__python_run_command(self._python_exec, cmd)
        except Exception as e:
            self.output.error("Error while running python command: {0}".format(e))
            raise RuntimeError("Error while executing python command.")


    def __python_get_version_nodot(self, python_exec):
        try:
            cmd = "from sys import *; print('{0}{1}'.format(version_info[0],version_info[1]))"
            return self.__python_run_command(self._python_exec, cmd)
        except Exception as e:
            self.output.error("Error while running python command: {0}".format(e))
            raise RuntimeError("Error while executing python command.")





#
# CMake default implementation
#


# reusable code for ConanFile
class CampCMakeBase(object):
    """
    BaseClass for Conan PythonRequires for packages that build with cmake.
    """

    source_subfolder = None
    build_subfolder = None

    def _configure_cmake(self):
        cmake = CMake(self)
        cmake.verbose = True

        def add_cmake_option(option, value):
            var_name = "{}".format(option).upper()
            value_str = "{}".format(value)
            var_value = "ON" if value_str == 'True' else "OFF" if value_str == 'False' else value_str
            cmake.definitions[var_name] = var_value

        for option, value in self.options.items():
            add_cmake_option(option, value)

        cmake.configure(source_folder=self.source_subfolder, build_folder=self.build_subfolder)
        return cmake

    def build(self):
        self._before_configure()
        cmake = self._configure_cmake()
        self._before_build(cmake)
        cmake.build()
        self._after_build()

    def package(self):
        cmake = self._configure_cmake()
        self._before_package(cmake)
        cmake.install()
        self._after_package()

    def package_info(self):
        self.cpp_info.libs = tools.collect_libs(self)
        self._after_package_info()



    # customization points

    def _before_configure(self):
        pass

    def _before_build(self, cmake):
        pass

    def _after_build(self):
        pass

    def _before_package(self, cmake):
        pass

    def _after_package(self):
        pass

    def _after_package_info(self):
        pass

class CommonConan(ConanFile):
    name = 'camp_common'
    upstream_version = '0.1'
    package_revision = ''
    version = "{0}{1}".format(upstream_version, package_revision)

    description = 'Helper functions for conan'
    url = 'https://github.com/TUM-CONAN/conan-camp-common'
    build_policy = 'missing'
