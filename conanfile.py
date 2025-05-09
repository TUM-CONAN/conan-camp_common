#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import re

from conan import ConanFile
from conan.tools.cmake import CMake, CMakeToolchain, cmake_layout, CMakeDeps
from conan.tools.files import collect_libs
from conan.tools.microsoft import is_msvc

from functools import update_wrapper
import platform

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
    if kwargs.get('is_posix', platform.system() == "Linux"):
        # CPU with 64-bit extensions, MMX, SSE, SSE2, SSE3, SSSE3, SSE4.1, SSE4.2,
        # POPCNT, AVX, AES, PCLMUL, FSGSBASE instruction set support.
        flags = '-march=sandybridge'
        flags += ' -mtune=generic'
        flags += ' -mfpmath=sse'
        return flags
    elif kwargs.get('is_macos', platform.system() == "Darwin"):
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
    if kwargs.get('is_posix', platform.system() == "Linux"):
        return '-O3 -fomit-frame-pointer -DNDEBUG'
    elif kwargs.get('is_windows', platform.system() == "Windows"):
        return '/O2 /Ob2 /MD /DNDEBUG'
    else:
        return ''


def get_release_cxx_flags(**kwargs):
    return get_release_c_flags(**kwargs)


def get_debug_c_flags(**kwargs):
    if kwargs.get('is_posix', platform.system() == "Linux"):
        if kwargs.get('compiler', None) == "nvcc":
            return '-O0 -g -D_DEBUG'
        else:
            return '-Og -g -D_DEBUG'
    elif kwargs.get('is_windows', platform.system() == "Windows"):
        return '/Ox /Oy- /Ob1 /Z7 /MDd /D_DEBUG'
    else:
        return ''


def get_debug_cxx_flags(**kwargs):
    return get_debug_c_flags(**kwargs)


def get_relwithdebinfo_c_flags(**kwargs):
    if kwargs.get('is_posix', platform.system() == "Linux"):
        return '-O3 -g -DNDEBUG'
    elif kwargs.get('is_windows', platform.system() == "Windows"):
        return get_release_c_flags(**kwargs) + ' /Z7'
    else:
        return ''


def get_relwithdebinfo_cxx_flags(**kwargs):
    return get_relwithdebinfo_c_flags(**kwargs)


def get_thorough_debug_c_flags(**kwargs):
    if kwargs.get('is_posix', platform.system() == "Linux"):
        return '-O0 -g3 -D_DEBUG'
    elif kwargs.get('is_windows', platform.system() == "Windows"):
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
# CUDA Defaults
#
def get_cuda_version():
    return ['11.0', '11.1', '11.2', '11.4', '11.5', '11.6', '11.7', '11.8', 
            '12.0', '12.1', '12.2', '12.4', '12.5', '12.6', '12.7', '12.8', 'None']


def get_cuda_arch():
    return ['6.0', '6.1', '7.0', '7.2', '7.5', '8.0', '8.6', '8.7', '9.0']


#
# Baseclass for CUDA Dependency
#

# somehow exporting does not provide access to package options
# so defining cuda_root only works with conan create, but not conan export ..
CUDA_ROOT_DEFAULT = None
if platform.system() == "Windows":
    CUDA_ROOT_DEFAULT = "C:\\Program Files\\NVIDIA GPU Computing Toolkit\\CUDA\\v11.8"
elif platform.system() == "Linux":
    CUDA_ROOT_DEFAULT = "/usr/local/cuda-11.8"


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
        if platform.system() == "Windows":
            return os.path.join(self._cuda_sdk_root, "lib", "x64")
        else:
            return os.path.join(self._cuda_sdk_root, "lib64")        

    @LazyProperty
    def _cuda_include_dir(self):
        return os.path.join(self._cuda_sdk_root, "include")

    # internal methods

    @property
    def _dynamic_lib_sufix(self):
        return "dylib" if self.settings.os == "Macos" else "so"

    @property
    def _cuda_runtime_dynamic_ldname(self):
        return "cudart.dll" if self.settings.os == "Windows" else "libcudart.{}".format(self._dynamic_lib_sufix)

    @property
    def _cuda_runtime_static_ldname(self):
        return "cudart_static.lib" if self.settings.os == "Windows" else "libcudart_static.a"

    @property
    def _cuda_runtime_ldname(self):
        return self._cuda_runtime_dynamic_ldname if self.options.get_safe("shared") else self._cuda_runtime_static_ldname

    def __cuda_get_sdk_root_and_version(self, cuda_version=None):
        cuda_sdk_root = None
        cuda_version_found = None

        supported_versions = reversed([v for v in get_cuda_version() if v != 'None'])
        find_cuda_versions = []
        if cuda_version is not None:
            if cuda_version not in supported_versions:
                raise ValueError("Unsupported CUDA SDK Version requested: {0}".format(cuda_version))
            find_cuda_versions = [cuda_version, ]
        else:
            find_cuda_versions = supported_versions

        if platform.system() == "Linux":
            for cv in find_cuda_versions:
                cuda_sdk_root = "/usr/local/cuda-{0}".format(cv)
                if os.path.exists(cuda_sdk_root):
                    cuda_sdk_root = cuda_sdk_root
                    cuda_version_found = cv
                    break
        if platform.system() == "Windows":
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
        self.run('"{0}" {1}'.format(nvcc_executable, command), stdout=output)
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

    works with two configuration options (defined in the [conf] settings of the profile)
    if use_custom_python is device, the system-python-command will be ignored:
    - camp.common:use_custom_python: 3.12
    - camp.common:system_python_command: /usr/bin/python3.10
    """


    @LazyProperty
    def _get_custom_python_version(self):
        ver = self.conf.get("user.camp.common:use_custom_python", default=None, check_type=str)
        if ver is not None:
            el = ver.split(".")
            assert(len(el) >= 2)
            return "{0}.{1}".format(el[0], el[1])

    @LazyProperty
    def _get_system_python_path(self):
        if self._get_custom_python_version is not None:
            return None

        default_cmd = "python3"
        if self.settings.os == "Windows":
            default_cmd = "python.exe"
        return self.conf.get("user.camp.common:system_python_command", default=default_cmd, check_type=str)

    @LazyProperty
    def _use_custom_python(self):
        return self._get_custom_python_version is not None

    @LazyProperty
    def _python_exec(self):
        cmd = None
        if self._use_custom_python:
            if not "cpython" in self.dependencies:
                raise RuntimeError("ConanFile does not have cpython as dependency, which is required when using a custom python interpreter")
            cpy_dep = self.dependencies["cpython"]
            cmd = cpy_dep.conf_info.get("user.cpython:python")
            if cmd is None:
                self.output.warn("could not retrieve 'user.cpython:python' conf_info from cpython dependency - trying to provide sensible default")
                if platform.system() == "Windows": # missing windows subsystem ..
                    cmd = os.path.join(self._get_cpython_dependency.package_folder, "bin", "python.exe")
                else:
                    cmd = os.path.join(self._get_cpython_dependency.package_folder, "bin", "python")
        else:
            cmd = self._get_system_python_path
        return self.__python_get_interpreter_fullpath(cmd)

    @LazyProperty
    def _python_version(self):
        if self._use_custom_python:
            return self._get_custom_python_version
        return self.__python_get_version(self._python_exec)

    @LazyProperty
    def _python_version_nodot(self):
        if self._use_custom_python:
            return self._get_custom_python_version.replace(".", "")
        return self.__python_get_version_nodot(self._python_exec)

    @LazyProperty
    def _python_lib(self):
        py_lib = None
        if platform.system() == "Windows":  # @todo: and not tools.os_info.detect_windows_subsystem():
            py_lib = self._python_stdlib
            if py_lib:
                py_lib = os.path.join(os.path.dirname(py_lib), "libs", "python" + self._python_version_nodot + ".lib")
        elif platform.system() == "Darwin":
            py_lib = os.path.join(self.__python_get_sysconfig_var('LIBDIR'), self.__python_get_sysconfig_var('LIBRARY'))
        else:
            py_lib = os.path.join(self.__python_get_sysconfig_var('LIBDIR'), self.__python_get_sysconfig_var('LDLIBRARY'))
        return py_lib

    @LazyProperty
    def _python_lib_ldname(self):
        py_lib_ldname = None
        if platform.system() == "Windows":  # @todo: and not tools.os_info.detect_windows_subsystem():
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

    # internal functions
    def __python_run_command(self, python_exec, command):
        output = StringIO()
        self.output.info('running python command: "{0}" -c "{1}"'.format(python_exec, command))
        self.run('"{0}" -c "{1}"'.format(python_exec, command), stdout=output)
        return output.getvalue().strip()

    def __python_get_interpreter_fullpath(self, command):
        if command is None:
            raise RuntimeError("Invalid python executable path: None")
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

    def generate(self):
        tc = CMakeToolchain(self)

        def add_cmake_option(option, value):
            var_name = "{}".format(option).upper()
            value_str = "{}".format(value)
            var_value = "ON" if value_str == 'True' else "OFF" if value_str == 'False' else value_str
            tc.variables[var_name] = var_value

        for option, value in self.options.items():
            add_cmake_option(option, value)
        # @todo: this is a hack to make cuda compile
        if is_msvc(self) and "vs_runtime" in tc.blocks.keys():
            tc.blocks.remove("vs_runtime")
        self._configure_toolchain(tc)
        tc.generate()

        deps = CMakeDeps(self)
        self._configure_cmakedeps(deps)
        deps.generate()
        self._extend_generate()

    def layout(self):
        cmake_layout(self)

    def build(self):
        cmake = CMake(self)
        self._before_configure()
        cmake.configure()
        self._before_build(cmake)
        cmake.build()
        self._after_build()

    def package(self):
        cmake = CMake(self)
        self._before_package(cmake)
        cmake.install()
        self._after_package()

    def package_info(self):
        self.cpp_info.libs = collect_libs(self)
        self._after_package_info()

    # customization points

    def _configure_toolchain(self, tc):
        pass

    def _configure_cmakedeps(self, deps):
        pass

    def _extend_generate(self):
        pass

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
    upstream_version = '0.6'
    package_revision = ''
    version = "{0}{1}".format(upstream_version, package_revision)

    package_type = "python-require"

    description = 'Helper functions for conan'
    url = 'https://github.com/TUM-CONAN/conan-camp-common'
    build_policy = 'missing'
