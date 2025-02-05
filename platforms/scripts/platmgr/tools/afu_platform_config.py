#!/usr/bin/env python3

#
# Copyright (c) 2017, Intel Corporation
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
# Redistributions of source code must retain the above copyright notice, this
# list of conditions and the following disclaimer.
#
# Redistributions in binary form must reproduce the above copyright notice,
# this list of conditions and the following disclaimer in the documentation
# and/or other materials provided with the distribution.
#
# Neither the name of the Intel Corporation nor the names of its contributors
# may be used to endorse or promote products derived from this software
# without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
# ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT OWNER OR CONTRIBUTORS BE
# LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
# CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
# SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
# INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
# CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
# ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.
#

#
# This script reads an AFU top-level interface specification that describes
# the module name and ports expected by an AFU.  It also reads a
# platform database that describes the top-level interface ports
# that the platform offers.  The script validates that the platform meets
# the requirements of the AFU and constructs a set of SystemVerilog header
# and interface files that describe the platform.  Files containing rules
# for loading the constructed headers and interfaces into either ASE or
# Quartus are also emitted.
#

import os
import sys
import glob
import argparse
import json
import pprint

from platmgr.lib.jsondb import jsondb
from platmgr.lib.emitcfg import emitConfig, emitSimConfig, emitQsfConfig
from platmgr.db.info import plat_db_root


def errorExit(msg):
    sys.stderr.write("\nError: " + msg + "\n")
    sys.exit(1)


#
# Walk the AFU's module-portments requirements and look for corresponding
# ports offered by the platform.
#
def matchAfuPorts(args, afu_ifc_db, platform_db):
    afu_ports = []

    afu_name = afu_ifc_db['file_name']
    plat_name = platform_db['file_name']

    if (not isinstance(afu_ifc_db['module-ports'], dict)):
        errorExit("module-ports is not a dictionary " +
                  "in {0}".format(afu_ifc_db['file_path']))
    if (not isinstance(platform_db['module-ports-offered'], dict)):
        errorExit("module-ports-offered is not a dictionary " +
                  "in {0}".format(platform_db['file_path']))

    if (args.verbose):
        print("Starting module ports match...")
        print("  AFU {0} requests:".format(afu_name))
        for k in sorted(afu_ifc_db['module-ports'].keys()):
            r = afu_ifc_db['module-ports'][k]
            print("    {0}:{1}".format(r['class'], r['interface']))
        print("  Platform {0} offers:".format(plat_name))
        for k in sorted(platform_db['module-ports-offered'].keys()):
            r = platform_db['module-ports-offered'][k]
            print("    {0}:{1}".format(r['class'], r['interface']))

    # Ports requested by the AFU
    for port in list(afu_ifc_db['module-ports'].values()):
        plat_match = None

        # Ports offered by the platform
        plat_key = port['class'] + '/' + port['interface']
        if (plat_key not in platform_db['module-ports-offered']):
            # Failed to find a match
            if (not port['optional']):
                errorExit(
                    "{0} needs port {1}:{2} that {3} doesn't offer".format(
                        afu_name, port['class'], port['interface'], plat_name))
        else:
            if (args.verbose):
                print("Found match for port {0}:{1}".format(
                    port['class'], port['interface']))

            plat_match = platform_db['module-ports-offered'][plat_key]

            # Found a potential match.
            match = {'afu': port, 'plat': plat_match}

            # For vector classes, do the offered sizes work?
            if (not port['vector'] and not plat_match['vector']):
                # Not a vector
                None
            elif (port['vector'] and not plat_match['vector']):
                # AFU wants a vector, but the platform doesn't offer one.
                errorExit(("{0} port {1}:{2} expects a vector but the " +
                           "platform {3} offers only a non-vector!").format(
                               afu_name, port['class'],
                               port['interface'], plat_name))
            elif (not port['vector'] and plat_match['vector']):
                # Platform provides a vector, but the AFU doesn't want one
                errorExit(("{0} port {1}:{2} expects a non-vector but the " +
                           "platform {3} offers only a vector!").format(
                               afu_name, port['class'],
                               port['interface'], plat_name))
            else:
                # Both are vectors.  Pick a size, starting with either the most
                # the platform will offer or the default number, depending on
                # whether the AFU requested a specific number.
                if ((port['max-entries'] == sys.maxsize) and
                    ('default-entries' in port) and
                    (port['default-entries'] >= plat_match['min-entries']) and
                    (port['default-entries'] <=
                     plat_match['max-entries'])):
                    entries = port['default-entries']
                elif ((port['max-entries'] == sys.maxsize) and
                      ('default-entries' in plat_match) and
                      (plat_match['default-entries'] >= port['min-entries'])):
                    entries = plat_match['default-entries']
                else:
                    entries = plat_match['max-entries']

                # Constrain the number to what the AFU can accept
                if (entries > port['max-entries']):
                    entries = port['max-entries']
                if (entries < port['min-entries']):
                    errorExit(("{0} port {1}:{2} requires more vector " +
                               "entries than {3} provides!").format(
                                   afu_name, port['class'], port['interface'],
                                   plat_name))
                if (entries < plat_match['min-entries']):
                    errorExit(("{0} port {1}:{2} requires more fewer " +
                               "entries than {3} provides!").format(
                                   afu_name, port['class'], port['interface'],
                                   plat_name))

                # Found an acceptable number of entries
                if (args.verbose):
                    print(
                        "  {0} vector length is {1}".format(plat_key, entries))
                match['num-entries'] = entries

            # Valid module port
            afu_ports.append(match)

    return afu_ports


#
# Return a dictionary describing the AFU's desired top-level interface.
#
def getAfuIfc(args):
    afu_ifc = dict()

    if (args.ifc):
        # Interface name specified on the command line
        afu_ifc['class'] = args.ifc
        afu_ifc['file_path'] = None
        afu_ifc['file_name'] = None
    else:
        # The AFU top-level interface was not specified explicitly.
        # Look for it in a JSON file.
        if (not args.src):
            errorExit("Either --ifc or --src must be specified.  See --help.")

        # Is the source argument a JSON file?
        if (os.path.isfile(args.src)):
            afu_json = args.src

        # Is the source argument a directory?
        elif (os.path.isdir(args.src)):
            # Find all the JSON files in the directory
            afu_json_list = [
                f for f in os.listdir(args.src) if f.endswith(".json")]
            if (len(afu_json_list) == 0):
                errorExit("AFU source directory " +
                          "({0}) has no JSON file!".format(args.src))
            if (len(afu_json_list) > 1):
                errorExit("AFU source directory ({0}) has ".format(args.src) +
                          "multiple JSON files.  The desired JSON file may " +
                          "be specified explicitly with --ifc.")

            # Found a JSON file
            afu_json = os.path.join(args.src, afu_json_list[0])

        else:
            errorExit("AFU source ({0}) not found!".format(args.src))

        # Parse file JSON file
        if (args.verbose):
            print("Loading AFU interface from {0}".format(afu_json))

        with open(afu_json) as f:
            try:
                data = json.load(f)
                f.close()
            except Exception:
                sys.stderr.write("\nError parsing JSON file {0}\n\n".format(
                    afu_json))
                raise
        try:
            afu_ifc = data['afu-image']['afu-top-interface']

            # *** Clean up legacy AFU JSON ***

            # The name 'module-ports' used to be 'module-arguments'.
            # Maintain compatibility with older AFUs.
            if ('module-arguments' in afu_ifc):
                afu_ifc['module-ports'] = afu_ifc.pop('module-arguments')

            # The interface 'class' used to be called 'name'.
            # Maintain compatibility with older AFUs.
            if ('name' in afu_ifc):
                afu_ifc['class'] = afu_ifc.pop('name')

            # Dereference the class to be sure it is present.
            afu_ifc_class = afu_ifc['class']
        except Exception:
            # The JSON file doesn't name the top-level interface.
            # Was a default specified on the command line?
            msg = "No afu-image:afu-top-interface:class found in " + afu_json
            if (args.default_ifc):
                afu_ifc = dict()
                afu_ifc['class'] = args.default_ifc
                print("Warning: " + msg)
                print("         Using default interface: {0}\n".format(
                    args.default_ifc))
            else:
                errorExit(msg)

        afu_ifc['file_path'] = afu_json
        afu_ifc['file_name'] = os.path.splitext(os.path.basename(afu_json))[0]

    if (args.verbose):
        print("AFU interface requested: {0}".format(afu_ifc))

    return afu_ifc


# Fields that in AFU interface that may be updated by a particular AFU's
# JSON file.
legal_afu_ifc_update_classes = {
    'default-entries',
    'max-entries',
    'min-entries',
    'optional',
    'params'
}


#
# An AFU's JSON database may override some parameters in the generic AFU
# interface description by specifying updates in the AFU's
#          afu-image:afu-top-interface:module-ports
# field.
#
# In addition to overriding, the AFU JSON may extend the base interface
# description by adding new port classes.  Without the ability to
# extend the port class list here we would have to enumerate all
# possible combinations of ports in the base interface classes.
#
def injectAfuIfcChanges(args, afu_ifc_db, afu_ifc_req):
    fname = afu_ifc_req['file_path']

    if ('module-ports' not in afu_ifc_req):
        return
    if (not isinstance(afu_ifc_req['module-ports'], list)):
        errorExit("module-ports is not a list in {0}".format(
            fname))

    # Walk all the updated classes
    for port in afu_ifc_req['module-ports']:
        # Is the port descriptor a dictionary?
        if (not isinstance(port, dict)):
            errorExit(("module-ports in {0} must be " +
                       "dictionaries ({1})").format(fname, port))

        if ('class' not in port):
            errorExit(("Each module-ports must have a class " +
                       "in {0}").format(fname))
        c = port['class']

        # Is the class already be present in the AFU interface?
        if (c not in afu_ifc_db['module-ports']):
            # No, this is a new addition to the base list of ports.
            # It must name an interface.
            if ('interface' not in port):
                errorExit(("module port {0} is missing 'interface' " +
                           "in {1}").format(port, fname))
            afu_ifc_db['module-ports'][c] = port
            if (args.verbose):
                print(("  AFU {0} adds new module-port class {1}").format(
                    fname, c))
        else:
            # Yes, this is an update of a port already defined.
            # Restrict the fields it may update.
            for k in list(port.keys()):
                if (k != 'class'):
                    # Only legal_afu_ifc_update_classes may be modified by the
                    # AFU's JSON database
                    if (k not in legal_afu_ifc_update_classes):
                        errorExit(
                            ("AFU may not update module-port class '{0}', " +
                             "field '{1}' ({2})").format(
                                 c, k, fname))

                    if (args.verbose):
                        print(("  AFU {0} overrides module-port class" +
                               " '{1}', field '{2}': {3}").format(
                                   fname, c, k, port[k]))

                    # Do the update
                    afu_ifc_db['module-ports'][c][k] = port[k]


#
# Dump a database for debugging.
#
def emitDebugJsonDb(args, name, db):
    # Path prefix for emitting configuration files
    f_prefix = ""
    if (args.tgt):
        f_prefix = args.tgt

    fn = os.path.join(f_prefix, 'debug_' + name + '.json')
    print("Writing debug {0}".format(fn))

    db.dump(fn)


#
# Dump a data structure for debugging.
#
def emitDebugData(args, name, data):
    # Path prefix for emitting configuration files
    f_prefix = ""
    if (args.tgt):
        f_prefix = args.tgt

    fn = os.path.join(f_prefix, 'debug_' + name + '.data')
    print("Writing debug {0}".format(fn))

    try:
        with open(fn, "w") as f:
            pprint.pprint(data, stream=f, indent=4)
    except Exception:
        errorExit("failed to open {0} for writing.".format(fn))


#
# Return a list of all platform names found on the search path.
#
def findPlatforms(db_path):
    platforms = set()
    # Walk all the directories
    for db_dir in db_path:
        # Look for JSON files in each directory
        for json_file in glob.glob(os.path.join(db_dir, "*.json")):
            try:
                with open(json_file, 'r') as f:
                    # Does it have a platform name field?
                    db = json.load(f)
                    platforms.add(db['platform-name'])
            except Exception:
                # Give up on this file if there is any error
                None

    return sorted(list(platforms))


#
# Return a list of all AFU top-level interface names found on the search path.
#
def findAfuIfcs(db_path):
    afus = set()
    # Walk all the directories
    for db_dir in db_path:
        # Look for JSON files in each directory
        for json_file in glob.glob(os.path.join(db_dir, "*.json")):
            try:
                with open(json_file, 'r') as f:
                    db = json.load(f)
                    # If it has a module-ports entry assume the file is
                    # valid
                    if ('module-ports' in db):
                        base = os.path.basename(json_file)
                        afus.add(os.path.splitext(base)[0])
            except Exception:
                # Give up on this file is any error
                None

    return sorted(list(afus))


#
# Compute a directory search path given an environment variable name.
# The final entry on the path is set to default_dir.
#
def getSearchPath(env_name, default_dir):
    path = []

    if (env_name in os.environ):
        # Break path string using ':' and drop empty entries
        path = [p for p in os.environ[env_name].split(':') if p]

    # Append the database directory shipped with a release if
    # the release containts hw/lib/platform/<default_dir>.
    if ('OPAE_PLATFORM_ROOT' in os.environ):
        release_db_dir = os.path.join(os.environ['OPAE_PLATFORM_ROOT'],
                                      'hw', 'lib', 'platform',
                                      default_dir)
        if (os.path.isdir(release_db_dir)):
            path.append(release_db_dir)

    # Append the default directory from OPAE SDK
    path.append(os.path.join(plat_db_root, default_dir))

    return path


#
# Does the release define platform components?
#
def getOfsPlatIfPath():
    # Documented variable, pointing to a platform release
    if ('OPAE_PLATFORM_ROOT' in os.environ):
        plat_dir = os.path.join(os.environ['OPAE_PLATFORM_ROOT'].rstrip('/'),
                                'hw/lib/build/platform/ofs_plat_if')
        if (os.path.isdir(plat_dir)):
            return plat_dir

    # Alternate method
    if ('BBS_LIB_PATH' in os.environ):
        plat_dir = os.path.join(os.environ['BBS_LIB_PATH'].rstrip('/'),
                                'build/platform/ofs_plat_if')
        if (os.path.isdir(plat_dir)):
            return plat_dir

    return None


def main():
    # Users can extend the AFU and platform database search paths beyond
    # the OPAE SDK defaults using environment variables.
    afu_top_ifc_db_path = getSearchPath(
        'OPAE_AFU_TOP_IFC_DB_PATH', 'afu_top_ifc_db')
    platform_db_path = getSearchPath('OPAE_PLATFORM_DB_PATH', 'platform_db')

    msg = '''
Given a platform and an AFU, afu_platform_config attempts to map the top-level
interfaces offered by the platform to the requirements of the AFU.  If the
AFU's requirements are satisfiable, afu_platform_config emits header files
that describe the interface.

Databases describe both top-level AFU and platform interfaces.  The search
paths for database files are configurable with environment variables using
standard colon separation between paths:

Platform database directories (OPAE_PLATFORM_DB_PATH):
'''
    for p in platform_db_path[:-1]:
        msg += '  ' + p + '\n'
    msg += '  ' + platform_db_path[-1] + ' [default]\n'

    platform_names = findPlatforms(platform_db_path)
    if (platform_names):
        msg += "\n  Platforms found:\n"
        for p in platform_names:
            msg += '    ' + p + '\n'

    msg += "\nAFU database directories (OPAE_AFU_TOP_IFC_DB_PATH):\n"
    for p in afu_top_ifc_db_path[:-1]:
        msg += '  ' + p + '\n'
    msg += '  ' + afu_top_ifc_db_path[-1] + ' [default]\n'

    afu_names = findAfuIfcs(afu_top_ifc_db_path)
    if (afu_names):
        msg += "\n  AFU top-level interfaces found:\n"
        for a in afu_names:
            msg += '    ' + a + '\n'

    parser = argparse.ArgumentParser(
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Match AFU top-level interface's requirements and a " +
                    "specific platform.",
        epilog=msg)

    # Positional arguments
    parser.add_argument(
        "platform",
        help="""Either the name of a platform or the name of a platform
                JSON file. If the argument is a platform name, the
                platform JSON file will be loaded from the platform
                database directory search path (see below).""")

    parser.add_argument(
        "-t", "--tgt",
        help="""Target directory to which configuration files will be written.
                Defaults to current working directory.""")

    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "-i", "--ifc",
        help="""The AFU's top-level interface class or the full pathname of a
                JSON top-level interface descriptor. (E.g. ccip_std_afu)""")
    group.add_argument(
        "-s", "--src",
        help="""The AFU sources, where a JSON file that specifies the AFU's
                top-level interface is found. Use either the --ifc argument
                or this one, but not both. The argument may either be the
                full path of a JSON file describing the application or the
                argument may be a directory in which the JSON file is found.
                If the argument is a directory, there must be exactly one
                JSON file in the directory.""")

    parser.add_argument(
        "--default-ifc",
        help="""The default top-level interface class if no interface is
                specified in the AFU's JSON descriptor.""")

    # Pick a default platform interface RTL tree. Start by looking for
    # the OFS platform tree in the currently configured release.
    ofs_plat_if_default = getOfsPlatIfPath()
    # If there is no current release or the release is old and does not
    # provide an OFS platform interface tree then resort to the interface
    # defined in the OPAE SDK.
    if_default = ofs_plat_if_default
    if not if_default:
        if_default = os.path.join(plat_db_root, "platform_if")

    parser.add_argument(
        "--platform_if", default=if_default,
        help="""The directory containing AFU top-level SystemVerilog
                interfaces. (Default: """ + if_default + ")")

    group = parser.add_mutually_exclusive_group()
    group.add_argument("--sim",
                       action="store_true",
                       default=False,
                       help="""Emit a configuration for RTL simulation.""")
    group.add_argument("--qsf",
                       action="store_true",
                       default=True,
                       help="""Emit a configuration for Quartus. (default)""")

    parser.add_argument(
        "--debug", action='store_true', default=False, help=argparse.SUPPRESS)

    # Verbose/quiet
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "-v", "--verbose", action="store_true", help="Verbose output")
    group.add_argument(
        "-q", "--quiet", action="store_true", help="Reduce output")
    args = parser.parse_args()

    if (args.sim):
        args.qsf = False

    # Get the AFU top-level interface request, either from the command
    # line or from the AFU source's JSON descriptor.
    afu_ifc_req = getAfuIfc(args)

    # If the AFU interface is ofs_plat_if then there must be a platform
    # defined and that platform must provide an ofs_plat_if tree.
    if (afu_ifc_req['class'] == 'ofs_plat_afu' and not ofs_plat_if_default):
        errorExit("AFU is type 'ofs_plat_afu' but the release is either " +
                  "not defined or does not provide\n" +
                  "       an ofs_plat_if.\n\n" +
                  "   *** Either OPAE_PLATFORM_ROOT is not set correctly " +
                  "or the release at\n" +
                  "   *** $OPAE_PLATFORM_ROOT is missing the directory " +
                  "hw/lib/build/platform/ofs_plat_if.")

    # Now that arguments are parsed, canonicalize the ofs_plat_if. If it
    # hasn't been changed from the default then make the path relative
    # to the platform directory. The tree will be copied into the AFU's build
    # tree. Using the local copy makes the AFU build tree easier to package.
    if (args.qsf and args.platform_if and
            (args.platform_if == ofs_plat_if_default)):
        # The generated script will set THIS_DIR to the script's directory
        args.platform_if = '${THIS_DIR}/ofs_plat_if'

    # Load the platform database
    platform = jsondb(args.platform, platform_db_path, 'platform', args.quiet)
    platform.canonicalize()

    # Load the platform default parameters
    platform_defaults = jsondb('platform_defaults', platform_db_path,
                               'platform-params', args.quiet)
    platform_defaults.canonicalize()

    # Load the AFU top-level interface database
    afu_ifc = jsondb(afu_ifc_req['class'], afu_top_ifc_db_path, 'AFU',
                     args.quiet)
    injectAfuIfcChanges(args, afu_ifc.db, afu_ifc_req)
    afu_ifc.canonicalize()

    if (args.debug):
        emitDebugJsonDb(args, 'afu_ifc_db', afu_ifc)
        emitDebugJsonDb(args, 'platform_db', platform)
        emitDebugJsonDb(args, 'platform_defaults_db', platform_defaults)

    # Match AFU port requirements to platform offerings
    afu_port_list = matchAfuPorts(args, afu_ifc.db, platform.db)
    if (args.debug):
        emitDebugData(args, 'afu_port_list', afu_port_list)

    # Emit platform configuration
    emitConfig(args, afu_ifc.db, platform.db,
               platform_defaults.db, afu_port_list)
    if (args.sim):
        emitSimConfig(args, afu_ifc.db, platform.db,
                      platform_defaults.db, afu_port_list)
    else:
        emitQsfConfig(args, afu_ifc.db, platform.db,
                      platform_defaults.db, afu_port_list)


if __name__ == "__main__":
    main()
