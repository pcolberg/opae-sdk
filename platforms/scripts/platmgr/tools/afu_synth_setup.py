#!/usr/bin/env python3
# Copyright(c) 2013-2017, Intel Corporation
#
# Redistribution  and  use  in source  and  binary  forms,  with  or  without
# modification, are permitted provided that the following conditions are met:
#
# * Redistributions of  source code  must retain the  above copyright notice,
#   this list of conditions and the following disclaimer.
# * Redistributions in binary form must reproduce the above copyright notice,
#   this list of conditions and the following disclaimer in the documentation
#   and/or other materials provided with the distribution.
# * Neither the name  of Intel Corporation  nor the names of its contributors
#   may be used to  endorse or promote  products derived  from this  software
#   without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING,  BUT NOT LIMITED TO,  THE
# IMPLIED WARRANTIES OF  MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
# ARE DISCLAIMED.  IN NO EVENT  SHALL THE COPYRIGHT OWNER  OR CONTRIBUTORS BE
# LIABLE  FOR  ANY  DIRECT,  INDIRECT,  INCIDENTAL,  SPECIAL,  EXEMPLARY,  OR
# CONSEQUENTIAL  DAMAGES  (INCLUDING,  BUT  NOT LIMITED  TO,  PROCUREMENT  OF
# SUBSTITUTE GOODS OR SERVICES;  LOSS OF USE,  DATA, OR PROFITS;  OR BUSINESS
# INTERRUPTION)  HOWEVER CAUSED  AND ON ANY THEORY  OF LIABILITY,  WHETHER IN
# CONTRACT,  STRICT LIABILITY,  OR TORT  (INCLUDING NEGLIGENCE  OR OTHERWISE)
# ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE,  EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.

#
# Create a Quartus build environment in a new directory and configure it for
# building an AFU.  Run "afu_synth_setup --help" for details.
#
# afu_synth_setup may be used by multiple FPGA platform releases.  Users must
# either specify the path to a release's hw/lib directory by setting the
# environment variable OPAE_FPGA_HW_LIB or by setting --lib.
#

import sys
import os
import stat
import errno
import shutil
import glob
import subprocess
from os.path import dirname, realpath, sep
import subprocess


def errorExit(msg):
    sys.stderr.write('Error: {0}\n'.format(msg))
    sys.exit(1)


#
# The hw/lib directory of a platform's release.  We find the hw/lib
# directory using the following search rules, in decreasing priority:
#
#   1. --lib argument to this script.
#   2. BBS_LIB_PATH:
#        We used to document this environment variable as the primary
#        pointer for scripts.
#   3. OPAE_PLATFORM_ROOT:
#        This variable replaces all pointers to a release directory,
#        starting with the discrete platform's 1.1 release.  The
#        hw/lib directory is ${OPAE_PLATFORM_ROOT}/hw/lib.
#
def getHWLibPath():
    if (args.lib is not None):
        hw_lib_dir = args.lib
        os.environ['BBS_LIB_PATH'] = hw_lib_dir
    elif ('BBS_LIB_PATH' in os.environ):
        # Legacy variable, shared with afu_sim_setup and HW releases
        hw_lib_dir = os.environ['BBS_LIB_PATH'].rstrip('/')
    elif ('OPAE_PLATFORM_ROOT' in os.environ):
        # Currently documented variable, pointing to a platform release
        hw_lib_dir = os.path.join(os.environ['OPAE_PLATFORM_ROOT'].rstrip('/'),
                                  'hw/lib')
    else:
        errorExit("Release hw/lib directory must be specified with " +
                  "OPAE_PLATFORM_ROOT, BBS_LIB_PATH or --lib")

    # Confirm that the path looks reasonable
    if (not os.path.exists(os.path.join(hw_lib_dir,
                                        'fme-platform-class.txt'))):
        errorExit("{0} is not a release hw/lib directory".format(hw_lib_dir))

    return hw_lib_dir


#
# Construct environment variables for a subprocess based on command line
# arguments.
#
def getCmdEnv():
    env = os.environ

    # Done if caller did not specify a non-standard library path.
    if (args.lib is None and 'BBS_LIB_PATH' not in os.environ):
        return env

    hw_lib_dir = getHWLibPath()

    # Define platform_db search path
    plat_db = os.path.join(hw_lib_dir, 'platform', 'platform_db')
    if (os.path.exists(plat_db)):
        env = updCmdEnv(env, 'OPAE_PLATFORM_DB_PATH', plat_db)

    # Define afu_top_ifc_db search path
    ifc_db = os.path.join(hw_lib_dir, 'platform', 'afu_top_ifc_db')
    if (os.path.exists(ifc_db)):
        env = updCmdEnv(env, 'OPAE_AFU_TOP_IFC_DB_PATH', ifc_db)

    return env


# Update a platform db search path in env
def updCmdEnv(env, key, value):
    if (key not in env):
        env[key] = value
    else:
        # Key already present.  The search list is colon separated.  Add the
        # new value if it wasn't already added.
        p = env[key].split(':')
        if (p[-1] != value):
            env[key] = env[key] + ':' + value

    return env


# Run a command
def commands_list(cmd, cwd=None, stdout=None):
    try:
        subprocess.check_call(cmd, cwd=cwd, stdout=stdout, env=getCmdEnv())
    except OSError as e:
        if e.errno == errno.ENOENT:
            msg = cmd[0] + " not found on PATH!"
            errorExit(msg)
        else:
            raise
    except subprocess.CalledProcessError as e:
        errorExit('"' + ' '.join(e.cmd) + '" failed')
    except AttributeError:
        sys.stderr.write('Error: Python 2.7 or greater required.\n')
        raise

    return None


# Run a command and get the output
def commands_list_getoutput(cmd, cwd=None):
    try:
        byte_out = subprocess.check_output(cmd, cwd=cwd, env=getCmdEnv())
        str_out = byte_out.decode()
    except OSError as e:
        if e.errno == errno.ENOENT:
            msg = cmd[0] + " not found on PATH!"
            errorExit(msg)
        else:
            raise
    except subprocess.CalledProcessError as e:
        sys.stderr.write(e.output)
        raise
    except AttributeError:
        sys.stderr.write('Error: Python 2.7 or greater required.\n')
        raise

    return str_out


# Remove a file, if it exists
def remove_file(f):
    if (os.path.isfile(f) or os.path.islink(f)):
        os.unlink(f)


# Remove a directory tree, if it exists
def remove_dir(d):
    if (os.path.isdir(d)):
        shutil.rmtree(d)


# Copy base build environment to target directory
def copy_build_env(hw_lib_dir, dst, force):
    # Target directory can't exist (unless force is set)
    dst = dst.rstrip(os.path.sep)
    if dst == '':
        errorExit('Target directory not set')

    if (os.path.exists(dst)):
        if (os.path.islink(dst)):
            errorExit('Target ({0}) is a link.'.format(dst))
        if (not force):
            errorExit('Target ({0}) already exists.'.format(dst))
        if (os.path.isdir(dst)):
            # Clean up inside the existing directory
            remove_dir(os.path.join(dst, 'build'))
            remove_dir(os.path.join(dst, 'scripts'))
            # Drop all top-level files in the 'hw' directory
            for f in glob.glob(os.path.join(dst, 'hw/*')):
                remove_file(f)
            # Drop 'hw' if it is a file
            remove_file(os.path.join(dst, 'hw'))
        else:
            os.remove(dst)
            os.mkdir(dst)
    else:
        os.mkdir(dst)

    # Copy build to target directory
    build_src = os.path.join(hw_lib_dir, 'build')
    build_dst = os.path.join(dst, 'build')
    print('Copying build from {0}...'.format(build_src))
    try:
        shutil.copytree(build_src, build_dst)
    except Exception:
        shutil.rmtree(dst)
        print('Failed to copy {0} to {1}'.format(build_src, build_dst))
        raise

    # Make target "hw" directory
    hw_dir = os.path.join(dst, 'hw')
    if (not os.path.isdir(hw_dir)):
        os.mkdir(hw_dir)


#
# Configure the build environment
#
def setup_build():
    # Does the sources file exist?
    if (not os.path.exists(args.sources)):
        errorExit("Sources file {0} not found.".format(args.sources))

    sources = args.sources

    # Get the JSON file
    try:
        cmd = ['rtl_src_config']
        if (os.path.isabs(sources)):
            cmd.append('--abs')
        cmd.append('--json')
        cmd.append(sources)
        json = commands_list_getoutput(cmd)
    except Exception:
        errorExit("Failed to read sources from {0}".format(sources))

    json = json.strip().split('\n')
    if (len(json) == 0):
        errorExit("No AFU JSON file found in {0}".format(sources))
    if (len(json) > 1):
        errorExit("More than one AFU JSON file found in {0}".format(sources))

    json = json[0]
    if (len(json) == 0):
        errorExit("No AFU JSON file found in {0}".format(sources))

    # json path will always be used one level below the destination's root.
    # Convert it if the path is relative.
    if (not os.path.isabs(json)):
        json = os.path.relpath(json, os.path.join(args.dst, 'build'))

    # Link to JSON file in hw tree
    os.symlink(json, os.path.join(args.dst, 'hw', os.path.basename(json)))

    # Where is the Quartus build directory?
    build_dir = os.path.join(args.dst, 'build')
    print('Configuring Quartus build directory: ' + build_dir)

    # Configure sources, generating hw/afu.qsf in the destination tree
    cmd = ['rtl_src_config']
    cmd.append('--qsf')
    cmd.append('--quiet')
    if (os.path.isabs(sources)):
        cmd.append('--abs')
        cmd.append(sources)
    else:
        cmd.append(os.path.relpath(sources, build_dir))
    with open(os.path.join(args.dst, 'hw/afu.qsf'), 'w') as f:
        commands_list(cmd, stdout=f, cwd=build_dir)

    # Configure the platform
    cmd = ['afu_platform_config', '--qsf', '--tgt', 'platform']
    cmd.append('--src=' + json)

    # Was the platform specified on the command line?
    if (args.platform):
        cmd.append(args.platform)
    else:
        # Get the platform from the release
        plat_class_file = os.path.join(getHWLibPath(),
                                       'fme-platform-class.txt')
        with open(plat_class_file) as f:
            cmd.append(f.read().strip())

    commands_list(cmd, cwd=build_dir)

    # Extract JSON info for Verilog
    cmd = ['afu_json_mgr', 'json-info', '--afu-json=' + json]
    cmd.append('--verilog-hdr=../hw/afu_json_info.vh')
    commands_list(cmd, cwd=build_dir)

    # Handle Qsys IPX file discovery
    setup_build_ipx(sources, build_dir)


# Construct the Qsys components.ipx file in the build directory, if needed.
def setup_build_ipx(sources, build_dir):
    # Get IPX files from source list
    cmd = ['rtl_src_config']
    cmd.append('--ipx')
    cmd.append('--quiet')
    if (os.path.isabs(sources)):
        cmd.append('--abs')
        cmd.append(sources)
    else:
        cmd.append(os.path.relpath(sources, build_dir))
    ipx = commands_list_getoutput(cmd, cwd=build_dir)
    ipx = ipx.strip()
    # Anything to do?
    if (len(ipx) == 0):
        return
    ipx = ipx.split('\n')

    # Emit components.ipx
    with open(os.path.join(build_dir, 'components.ipx'), 'w') as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n')
        f.write('<library>\n')
        for p in ipx:
            # Just point to the directory containing the IPX file
            ipx_path = os.path.dirname(p)
            f.write('  <path path="{0}/*" />\n'.format(ipx_path))
        f.write('</library>\n')


def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="""Generate a Quartus build environment for an AFU.
                       A build environment is instantiated from a release
                       and then configured for the specified AFU.
                       AFU source files are specified in a text file that is
                       parsed by rtl_src_config, which is part of the
                       OPAE base environment.""")

    parser.add_argument('-s', '--sources', required=1,
                        help="""AFU source specification file that will be
                                passed to rtl_src_config.  See "rtl_src_config
                                --help" for the file's syntax.  rtl_src_config
                                translates the source list into either Quartus
                                or RTL simulator syntax.""")
    parser.add_argument('-p', '--platform', default=None,
                        help="""FPGA platform name.""")
    parser.add_argument('-l', '--lib', default=None,
                        help="""FPGA platform release hw/lib directory.  If
                                not specified, the environment variables
                                OPAE_FPGA_HW_LIB and then BBS_LIB_PATH are
                                checked.""")
    parser.add_argument('-f', '--force',
                        action='store_true',
                        help="""Overwrite target directory if it exists.""")
    parser.add_argument('dst',
                        help="""Target directory path (directory must
                                not exist).""")

    global args
    args = parser.parse_args()

    # Where is the base environment
    hw_lib_dir = getHWLibPath()

    copy_build_env(hw_lib_dir, args.dst, args.force)
    setup_build()


if __name__ == '__main__':
    main()
