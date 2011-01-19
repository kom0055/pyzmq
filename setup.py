#!/usr/bin/env python

#
#    Copyright (c) 2010 Brian E. Granger
#
#    This file is part of pyzmq.
#
#    pyzmq is free software; you can redistribute it and/or modify it under
#    the terms of the Lesser GNU General Public License as published by
#    the Free Software Foundation; either version 3 of the License, or
#    (at your option) any later version.
#
#    pyzmq is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    Lesser GNU General Public License for more details.
#
#    You should have received a copy of the Lesser GNU General Public License
#    along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
#    The `configure` subcommand is copied and adaped from h5py
#    h5py source used under the New BSD license
#
#    h5py: <http://code.google.com/p/h5py/>
#    BSD license: <http://www.opensource.org/licenses/bsd-license.php>
#

#-----------------------------------------------------------------------------
# Imports
#-----------------------------------------------------------------------------
from __future__ import with_statement

import os, sys, shutil
from traceback import print_exc

from distutils.core import setup, Command
from distutils.ccompiler import get_default_compiler
from distutils.extension import Extension
from distutils.command.sdist import sdist
from distutils.command.build_ext import build_ext

from unittest import TextTestRunner, TestLoader
from glob import glob
from os.path import splitext, basename, join as pjoin

from subprocess import Popen, PIPE
import logging

try:
    from configparser import ConfigParser
except:
    from ConfigParser import ConfigParser

try:
    import nose
except ImportError:
    nose = None

# local script imports:
import detect

#-----------------------------------------------------------------------------
# Flags
#-----------------------------------------------------------------------------
# ignore unused-function and strict-aliasing warnings, of which there
# will be many from the Cython generated code:
# note that this is only for gcc-style compilers
if get_default_compiler() in ('unix', 'mingw32'):
    ignore_common_warnings=True
else:
    ignore_common_warnings=False

release = False # flag for whether to include *.c in package_data

# the minimum zeromq version this will work against:
min_zmq = (2,1,0)
#-----------------------------------------------------------------------------
# Configuration (adapted from h5py: http://h5py.googlecode.com)
#-----------------------------------------------------------------------------
logger = logging.getLogger()
logger.addHandler(logging.StreamHandler(sys.stderr))
# --- Convenience functions --------------------------------------------------

def debug(what):
    pass

def fatal(instring, code=1):
    logger.error("Fatal: "+instring)
    exit(code)

def warn(instring):
    logger.error("Warning: "+instring)

def localpath(*args):
    return os.path.abspath(reduce(pjoin, (os.path.dirname(__file__),)+args))

def loadpickle(name):
    """ Load object from pickle file, or None if it can't be opened """
    import pickle
    name = pjoin('conf', name)
    try:
        f = open(name,'rb')
    except IOError:
        # raise
        return None
    try:
        return pickle.load(f)
    except Exception:
        # raise
        return None
    finally:
        f.close()

def savepickle(name, data):
    """ Save to pickle file, exiting if it can't be written """
    import pickle
    if not os.path.exists('conf'):
        os.mkdir('conf')
    name = pjoin('conf', name)
    try:
        f = open(name, 'wb')
    except IOError:
        fatal("Can't open pickle file \"%s\" for writing" % name)
    try:
        pickle.dump(data, f, 0)
    finally:
        f.close()

def v_str(v_tuple):
    """turn (2,0,1) into '2.0.1'."""
    return ".".join(str(x) for x in v_tuple)

# --- Try to discover path ---

def discover_settings():
    """ Discover custom settings for ZMQ path"""

    def get_eargs():
        """ Look for options in environment vars """

        settings = {}

        zmq = os.environ.get("ZMQ_DIR", '')
        if zmq != '':
            debug("Found environ var ZMQ_DIR=%s" % zmq)
            settings['zmq'] = zmq

        return settings

    def get_cfg_args():
        """ Look for options in setup.cfg """

        settings = {}
        zmq = ''
        if not os.path.exists('setup.cfg'):
            return settings
        cfg = ConfigParser()
        cfg.read('setup.cfg')
        if 'build_ext' in cfg.sections() and \
                    cfg.has_option('build_ext', 'include_dirs'):
            includes = cfg.get('build_ext', 'include_dirs')
            include = includes.split(os.pathsep)[0]
            if include.endswith('include') and os.path.isdir(include):
                zmq = include[:-8]
        if zmq != '':
            debug("Found ZMQ=%s in setup.cfg" % zmq)
            settings['zmq'] = zmq

        return settings

    def get_cargs():
        """ Look for global options in the command line """
        settings = loadpickle('buildconf.pickle')
        if settings is None:  settings = {}
        for arg in sys.argv[:]:
            if arg.find('--zmq=') == 0:
                zmq = arg.split('=')[-1]
                if zmq.lower() == 'default':
                    settings.pop('zmq', None)
                else:
                    settings['zmq'] = zmq
                sys.argv.remove(arg)
        savepickle('buildconf.pickle', settings)
        return settings

    settings = get_cfg_args()       # lowest priority
    settings.update(get_eargs())
    settings.update(get_cargs())    # highest priority
    return settings.get('zmq')

ZMQ = None
for cmd in ['install', 'build', 'build_ext', 'configure']:
    if cmd in sys.argv:
        ZMQ = discover_settings()
        break

if ZMQ is not None and not os.path.exists(ZMQ):
    warn("ZMQ directory \"%s\" does not appear to exist" % ZMQ)

# --- compiler settings -------------------------------------------------

if sys.platform.startswith('win'):
    COMPILER_SETTINGS = {
        'libraries'     : ['libzmq'],
        'include_dirs'  : [],
        'library_dirs'  : [],
    }
    if ZMQ is not None:
        COMPILER_SETTINGS['include_dirs'] += [pjoin(ZMQ, 'include')]
        COMPILER_SETTINGS['library_dirs'] += [pjoin(ZMQ, 'lib')]
else:
    COMPILER_SETTINGS = {
       'libraries'      : ['zmq'],
       'include_dirs'   : [],
       'library_dirs'   : [],
    }
    if ZMQ is not None:
        COMPILER_SETTINGS['include_dirs'] += [pjoin(ZMQ, 'include')]
        COMPILER_SETTINGS['library_dirs'] += [pjoin(ZMQ, 'lib')]
    elif sys.platform == 'darwin' and os.path.isdir('/opt/local/lib'):
        # allow macports default
        COMPILER_SETTINGS['include_dirs'] += ['/opt/local/include']
        COMPILER_SETTINGS['library_dirs'] += ['/opt/local/lib']
    COMPILER_SETTINGS['runtime_library_dirs'] = [os.path.abspath(x) for x in COMPILER_SETTINGS['library_dirs']]


#-----------------------------------------------------------------------------
# Extra commands
#-----------------------------------------------------------------------------

class configure(Command):
    """Configure command adapted from h5py"""

    description = "Discover ZMQ version and features"

    # DON'T REMOVE: distutils demands these be here even if they do nothing.
    user_options = []
    boolean_options = []
    def initialize_options(self):
        pass
    def finalize_options(self):
        pass

    tempdir = 'detect'

    def create_tempdir(self):
        self.erase_tempdir()
        os.mkdir(self.tempdir)
        if sys.platform.startswith('win'):
            # fetch libzmq.dll into local dir
            if ZMQ is None:
                fatal("ZMQ directory must be specified on Windows via setup.cfg or 'python setup.py configure --zmq=/path/to/zeromq2'")
            shutil.copy(pjoin(ZMQ, 'lib', 'libzmq.dll'), pjoin(self.tempdir, 'libzmq.dll'))

    def erase_tempdir(self):
        import shutil
        try:
            shutil.rmtree(self.tempdir)
        except Exception:
            pass

    def getcached(self):
        return loadpickle('configure.pickle')

    def check_zmq_version(self):
        zmq = ZMQ
        if zmq is not None and not os.path.isdir(zmq):
            fatal("Custom zmq directory \"%s\" does not exist" % zmq)

        config = self.getcached()
        if config is None or config['options'] != COMPILER_SETTINGS:
            self.run()
            config = self.config

        vers = config['vers']
        vs = v_str(vers)
        if vers < min_zmq:
            fatal("Detected ZMQ version: %s, but depend on zmq >= %s"%(
                    vs, v_str(min_zmq))
                    +'\n       Using ZMQ=%s'%(zmq or 'unspecified'))
            fatal()
        pyzmq_version = extract_version().strip('abcdefghijklmnopqrstuvwxyz')

        if vs < pyzmq_version:
            warn("Detected ZMQ version: %s, but pyzmq is based on zmq %s."%(
                    vs, pyzmq_version))
            warn("Some features may be missing or broken.")
            print('*'*42)

        if sys.platform.startswith('win'):
            # fetch libzmq.dll into local dir
            if zmq is None:
                fatal("ZMQ directory must be specified on Windows via setup.cfg or 'python setup.py configure --zmq=/path/to/zeromq2'")
            shutil.copy(pjoin(zmq, 'lib', 'libzmq.dll'), localpath('zmq','libzmq.dll'))

    def run(self):
        self.create_tempdir()
        try:
            print ("*"*42)
            print ("Configure: Autodetecting ZMQ settings...")
            print ("    Custom ZMQ dir:       %s" % (ZMQ,))
            config = detect.detect_zmq(self.tempdir, **COMPILER_SETTINGS)
            savepickle('configure.pickle', config)
        except Exception:
            logger.error("""
    Failed to compile ZMQ test program.  Please check to make sure:

    * You have a C compiler installed
    * A development version of Python is installed (including header files)
    * A development version of ZeroMQ >= 2.1.0 is installed (including header files)
    * If ZMQ is not in a default location, supply the argument --zmq=<path>""")
            raise
        else:
            print ("    ZMQ version detected: %s" % v_str(config['vers']))
        finally:
            print ("*"*42)
            self.erase_tempdir()
        self.config = config

class TestCommand(Command):
    """Custom distutils command to run the test suite."""

    user_options = [ ]

    def initialize_options(self):
        self._dir = os.getcwd()

    def finalize_options(self):
        pass
    
    def run_nose(self):
        """Run the test suite with nose."""
        return nose.core.TestProgram(argv=["", '-vvs', pjoin(self._dir, 'zmq', 'tests')])
    
    def run_unittest(self):
        """Finds all the tests modules in zmq/tests/ and runs them."""
        testfiles = [ ]
        for t in glob(pjoin(self._dir, 'zmq', 'tests', '*.py')):
            name = splitext(basename(t))[0]
            if name.startswith('test_'):
                testfiles.append('.'.join(
                    ['zmq.tests', name])
                )
        tests = TestLoader().loadTestsFromNames(testfiles)
        t = TextTestRunner(verbosity = 2)
        t.run(tests)
    
    def run(self):
        """Run the test suite, with nose, or unittest if nose is unavailable"""
        # crude check for inplace build:
        try:
            import zmq
        except ImportError:
            print_exc()
            fatal('\n       '.join(["Could not import zmq!",
            "You must build pyzmq with 'python setup.py build_ext --inplace' for 'python setup.py test' to work.",
            "If you did build pyzmq in-place, then this is a real error."]))
            sys.exit(1)
        
        if nose is None:
            print ("nose unavailable, falling back on unittest. Skipped tests will appear as ERRORs.")
            return self.run_unittest()
        else:
            return self.run_nose()

class GitRevisionCommand(Command):
    """find the current git revision and add it to zmq.core.verion.__revision__"""
    
    user_options = [ ]
    
    def initialize_options(self):
        self.version_pyx = pjoin('zmq','core','version.pyx')
    
    def run(self):
        try:
            p = Popen('git log -1'.split(), stdin=PIPE, stdout=PIPE, stderr=PIPE)
        except IOError:
            print ("No git found, skipping git revision")
            return
        
        if p.wait():
            print ("checking git branch failed")
            print (p.stderr.read())
            return
        
        line = p.stdout.readline().strip()
        if not line.startswith('commit'):
            print ("bad commit line: %r"%line)
            return
        
        rev = line.split()[-1]
        
        # now that we have the git revision, we can apply it to version.pyx
        with open(self.version_pyx) as f:
            lines = f.readlines()
        
        for i,line in enumerate(lines):
            if line.startswith('__revision__'):
                lines[i] = "__revision__ = '%s'\n"%rev
                break
        with open(self.version_pyx, 'w') as f:
            f.writelines(lines)
    
    def finalize_options(self):
        pass

class CleanCommand(Command):
    """Custom distutils command to clean the .so and .pyc files."""

    user_options = [ ]

    def initialize_options(self):
        self._clean_me = []
        self._clean_trees = []
        for root, dirs, files in os.walk('zmq'):
            for f in files:
                if os.path.splitext(f)[-1] in ('.pyc', '.so', '.o', '.pyd'):
                    self._clean_me.append(pjoin(root, f))
        for d in [ 'build' ]:
            if os.path.isdir(d):
                self._clean_trees.append(d)


    def finalize_options(self):
        pass

    def run(self):
        for clean_me in self._clean_me:
            try:
                os.unlink(clean_me)
            except:
                pass
        for clean_tree in self._clean_trees:
            try:
                shutil.rmtree(clean_tree)
            except:
                pass


class CheckSDist(sdist):
    """Custom sdist that ensures Cython has compiled all pyx files to c."""

    def initialize_options(self):
        sdist.initialize_options(self)
        self._pyxfiles = []
        for root, dirs, files in os.walk('zmq'):
            for f in files:
                if f.endswith('.pyx'):
                    self._pyxfiles.append(pjoin(root, f))
    def run(self):
        if 'cython' in cmdclass:
            self.run_command('cython')
        else:
            for pyxfile in self._pyxfiles:
                cfile = pyxfile[:-3]+'c'
                msg = "C-source file '%s' not found."%(cfile)+\
                " Run 'setup.py cython' before sdist."
                assert os.path.isfile(cfile), msg
        sdist.run(self)

class CheckingBuildExt(build_ext):
    """Subclass build_ext to get clearer report if Cython is neccessary."""
    
    def check_cython_extensions(self, extensions):
        for ext in extensions:
          for src in ext.sources:
            if not os.path.exists(src):
                raise IOError('',
                """Cython-generated file '%s' not found.
                Cython is required to compile pyzmq from a development branch.
                Please install Cython or download a release package of pyzmq.
                """%src)
    
    def build_extensions(self):
        self.check_cython_extensions(self.extensions)
        self.check_extensions_list(self.extensions)
        
        for ext in self.extensions:
            self.build_extension(ext)
    
    def run(self):
        # check version, to prevent confusing undefined constant errors
        # check_zmq_version(min_zmq)
        configure = self.distribution.get_command_obj('configure')
        configure.check_zmq_version()
        build_ext.run(self)
    

#-----------------------------------------------------------------------------
# Suppress Common warnings
#-----------------------------------------------------------------------------

extra_flags = []
if ignore_common_warnings:
    for warning in ('unused-function', 'strict-aliasing'):
        extra_flags.append('-Wno-'+warning)

COMPILER_SETTINGS['extra_compile_args'] = extra_flags

#-----------------------------------------------------------------------------
# Extensions
#-----------------------------------------------------------------------------

cmdclass = {'test':TestCommand, 'clean':CleanCommand, 'revision':GitRevisionCommand,
            'configure': configure}

COMPILER_SETTINGS['include_dirs'] += [pjoin('zmq', sub) for sub in ('utils','core','devices')]

def pxd(subdir, name):
    return os.path.abspath(pjoin('zmq', subdir, name+'.pxd'))

def pyx(subdir, name):
    return os.path.abspath(pjoin('zmq', subdir, name+'.pyx'))

def dotc(subdir, name):
    return os.path.abspath(pjoin('zmq', subdir, name+'.c'))

czmq = pxd('core', 'czmq')
buffers = pxd('utils', 'buffers')
message = pxd('core', 'message')
context = pxd('core', 'context')
socket = pxd('core', 'socket')

submodules = dict(
    core = {'constants': [czmq],
            'error':[czmq],
            'poll':[czmq],
            'stopwatch':[czmq, pxd('core','stopwatch')],
            'context':[socket, context, czmq],
            'message':[czmq, buffers, message],
            'socket':[context, message, socket, czmq, buffers],
            'device':[czmq],
            'version':[czmq],
    },
    devices = {
            'monitoredqueue':[buffers, czmq],
    },
    utils = {
            'initthreads':[czmq]
    }
)

try:
    from Cython.Distutils import build_ext
    cython=True
except ImportError:
    cython=False
    suffix = '.c'
    cmdclass['build_ext'] = CheckingBuildExt
else:
    
    suffix = '.pyx'
    
    class CythonCommand(build_ext):
        """Custom distutils command subclassed from Cython.Distutils.build_ext
        to compile pyx->c, and stop there. All this does is override the 
        C-compile method build_extension() with a no-op."""
        def build_extension(self, ext):
            pass
    
    class zbuild_ext(build_ext):
        def run(self):
            configure = self.distribution.get_command_obj('configure')
            configure.check_zmq_version()
            return build_ext.run(self)
    
    cmdclass['cython'] = CythonCommand
    cmdclass['build_ext'] =  zbuild_ext
    cmdclass['sdist'] =  CheckSDist

extensions = []
for submod, packages in submodules.items():
    for pkg in sorted(packages):
        sources = [pjoin('zmq', submod, pkg+suffix)]
        if suffix == '.pyx':
            sources.extend(packages[pkg])
        ext = Extension(
            'zmq.%s.%s'%(submod, pkg),
            sources = sources,
            **COMPILER_SETTINGS
        )
        extensions.append(ext)

#
package_data = {'zmq':['*.pxd'],
                'zmq.core':['*.pxd'],
                'zmq.devices':['*.pxd'],
                'zmq.utils':['*.pxd', '*.h'],
}

if release:
    for pkg,data in package_data.items():
        data.append('*.c')

if sys.platform.startswith('win'):
    package_data['zmq'].append('libzmq.dll')

def extract_version():
    """extract pyzmq version from core/version.pyx, so it's not multiply defined"""
    with open(pjoin('zmq', 'core', 'version.pyx')) as f:
        line = f.readline()
        while not line.startswith("__version__"):
            line = f.readline()
    exec(line, globals())
    return __version__

#-----------------------------------------------------------------------------
# Main setup
#-----------------------------------------------------------------------------

long_desc = \
"""
PyZMQ is a lightweight and super-fast messaging library built on top of
the ZeroMQ library (http://www.zeromq.org). 
"""

setup(
    name = "pyzmq",
    version = extract_version(),
    packages = ['zmq', 'zmq.tests', 'zmq.eventloop', 'zmq.log', 'zmq.core',
                'zmq.devices', 'zmq.utils'],
    ext_modules = extensions,
    package_data = package_data,
    author = "Brian E. Granger",
    author_email = "ellisonbg@gmail.com",
    url = 'http://github.com/zeromq/pyzmq',
    download_url = 'http://github.com/zeromq/pyzmq/downloads',
    description = "Python bindings for 0MQ.",
    long_description = long_desc, 
    license = "LGPL",
    cmdclass = cmdclass,
    classifiers = [
        'Development Status :: 5 - Production/Stable',
        'Intended Audience :: Developers',
        'Intended Audience :: Financial and Insurance Industry',
        'Intended Audience :: Science/Research',
        'Intended Audience :: System Administrators',
        'License :: OSI Approved :: GNU Library or Lesser General Public License (LGPL)',
        'Operating System :: MacOS :: MacOS X',
        'Operating System :: Microsoft :: Windows',
        'Operating System :: POSIX',
        'Topic :: System :: Networking'
    ]
)

