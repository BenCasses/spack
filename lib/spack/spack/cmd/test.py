# Copyright 2013-2020 Lawrence Livermore National Security, LLC and other
# Spack Project Developers. See the top-level COPYRIGHT file for details.
#
# SPDX-License-Identifier: (Apache-2.0 OR MIT)

from __future__ import print_function
import os
import argparse
import textwrap
import datetime
import fnmatch
import re
import shutil
import glob

import llnl.util.tty as tty
import llnl.util.filesystem as fs

import spack.environment as ev
import spack.cmd
import spack.cmd.find
from spack.main import SpackCommand
import spack.cmd.common.arguments as arguments
import spack.report
import spack.package

from spack.util.executable import Executable

description = "run spack's tests for an install"
section = "administrator"
level = "long"


def setup_parser(subparser):
    sp = subparser.add_subparsers(metavar='SUBCOMMAND', dest='test_command')

    # Run
    run_parser = sp.add_parser('run', help=test_run.__doc__)

    name_help_msg = "Name the test for subsequent access."
    name_help_msg += " Default is the timestamp of the run formatted"
    name_help_msg += " 'YYYY-MM-DD_HH:MM:SS'"
    run_parser.add_argument('-n', '--name', help=name_help_msg)

    run_parser.add_argument(
        '--fail-fast', action='store_true',
        help="Stop tests for each package after the first failure."
    )
    run_parser.add_argument(
        '--fail-first', action='store_true',
        help="Stop after the first failed package."
    )
    run_parser.add_argument(
        '--keep-stage',
        action='store_true',
        help='Keep testing directory for debugging'
    )
    run_parser.add_argument(
        '--log-format',
        default=None,
        choices=spack.report.valid_formats,
        help="format to be used for log files"
    )
    run_parser.add_argument(
        '--log-file',
        default=None,
        help="filename for the log file. if not passed a default will be used"
    )
    arguments.add_cdash_args(run_parser, False)
    run_parser.add_argument(
        '--help-cdash',
        action='store_true',
        help="Show usage instructions for CDash reporting"
    )

    length_group = run_parser.add_mutually_exclusive_group()
    length_group.add_argument(
        '--smoke', action='store_true', dest='smoke_test', default=True,
        help='run smoke tests (default)')
    length_group.add_argument(
        '--capability', action='store_false', dest='smoke_test', default=True,
        help='run full capability tests using pavilion')

    cd_group = run_parser.add_mutually_exclusive_group()
    arguments.add_common_arguments(cd_group, ['clean', 'dirty'])

    arguments.add_common_arguments(run_parser, ['installed_specs'])

    # List
    list_parser = sp.add_parser('list', help=test_list.__doc__)
    list_parser.add_argument(
        'testnames', nargs=argparse.REMAINDER,
        help='optional test or spec names to narrow results.')

    # Status
    status_parser = sp.add_parser('status', help=test_status.__doc__)
    status_parser.add_argument(
        'testnames', nargs=argparse.REMAINDER,
        help='optional test or spec names to narrow results.')


    # Results
    results_parser = sp.add_parser('results', help=test_results.__doc__)
    results_parser.add_argument(
        'testnames', nargs=argparse.REMAINDER,
        help='optional test or spec names to narrow results.')

    # Remove
    remove_parser = sp.add_parser('remove', help=test_remove.__doc__)
    remove_parser.add_argument(
        'testnames', nargs=argparse.REMAINDER,
        help='optional test or spec names to narrow results.')

# check for and install pavilion2
# TODO use the database to see if any pavilion2 is installed
#   spack.store.db.query("pavilion2")
#   might have to make it a spec first
# if not installed, use spack python cmd which to see if it exists at all
# only install if neither
def _install_pavilion():
    pavspec = spack.spec.Spec('pavilion2').concretized()
    pav_path = os.path.join(pavspec.prefix.bin, 'pav')
    pavspec.package.do_install()
    if not pavspec.package.installed or not os.path.exists(pav_path):
        raise InstallError('Failed to install Pavilion 2')
    return Executable(pav_path)

# is pavilion2 installed?
# TODO some repetition with the above
def _check_pavilion():
    pavspec = spack.spec.Spec('pavilion2').concretized()
    pav_path = os.path.join(pavspec.prefix.bin, 'pav')
    if pavspec.package.installed:
        return Executable(pav_path)
    else:
        return None

# get Pavilion2 tests from aspec 
# TODO look in the dir with package.py
def _get_pav_tests( aspec ):
    if os.path.isdir(aspec.prefix.pavilion) and \
       os.path.isdir(aspec.prefix.pavilion.tests) and \
       os.path.isdir(aspec.prefix.pavilion.test_src):
       return glob.glob(aspec.prefix.pavilion.tests + "/*.yaml" )
    return []

def test_run(args):
    """Run tests for the specified installed packages

    If no specs are listed, run tests for all packages in the current
    environment or all installed packages if there is no active environment.
    """

    # cdash help option
    if args.help_cdash:
        parser = argparse.ArgumentParser(
            formatter_class=argparse.RawDescriptionHelpFormatter,
            epilog=textwrap.dedent('''\
environment variables:
  SPACK_CDASH_AUTH_TOKEN
                        authentication token to present to CDash
                        '''))
        arguments.add_cdash_args(parser, True)
        parser.print_help()
        return

    # set config option for fail-fast
    if args.fail_fast:
        spack.config.set('config:fail_fast', True, scope='command_line')

    # record test time; this will be default test name
    now = datetime.datetime.now()
    test_name = args.name or now.strftime('%Y-%m-%d_%H:%M:%S')
    tty.msg("Spack test %s" % test_name)

    # Get specs to test
    env = ev.get_env(args, 'test')
    hashes = env.all_hashes() if env else None

    specs = spack.store.db.query_local(None, hashes=hashes)

    # limit to specs containing the given spec names
    if args.specs:
        specs = [ s for s in specs if any([n in str(s) for n in args.specs])]

    # Set up reporter
    setattr(args, 'package', [s.format() for s in specs])
    reporter = spack.report.collect_info(
        spack.package.PackageBase, 'do_test', args.log_format, args)
    if not reporter.filename:
        if args.log_file:
            if os.path.isabs(args.log_file):
                log_file = args.log_file
            else:
                log_dir = os.getcwd()
                log_file = os.path.join(log_dir, args.log_file)
        else:
            log_file = os.path.join(
                os.getcwd(),
                'test-%s' % test_name)
        reporter.filename = log_file
    reporter.specs = specs

    # test_stage_dir
    stage = get_stage(test_name)
    fs.mkdirp(stage)

    with reporter('test', stage):
        if args.smoke_test:
            for spec in specs:
                try:
                    spec.package.do_test(
                        name=test_name,
                        remove_directory=not args.keep_stage,
                        dirty=args.dirty)
                    with open(_get_results_file(test_name), 'a') as f:
                        f.write("%s PASSED\n" % spec.format("{name}-{version}-{hash:7}"))
                except BaseException:
                    with open(_get_results_file(test_name), 'a') as f:
                        f.write("%s FAILED\n" % spec.format("{name}-{version}-{hash:7}"))
                    if args.fail_first:
                        break
        else:
            pav = _check_pavilion()
            if not pav:
                pav = _install_pavilion()

            if args.name:
                testnames = args.name.split()
            else:
                testnames = ["spack"]

            # TODO construct a list of tests to run
            #   instead of not ranatest, check if list is empty
            ranatest = None
            for spec in specs:
                tests = [ os.path.basename( t ).split(".")[0] for t in _get_pav_tests(spec) ]
                for test in tests:
                    for testname in testnames:
# but only run a given test once
# avoid running the same test twice in a row if it matches twice
                        if testname in test and not ranatest == str(spec)+str(test):
                            tty.msg("launching test \"" + test + "\" for " + str(spec))
                            os.environ["PAV_CONFIG_DIR"] = spec.prefix.pavilion
                            output = pav('run', test, output=str, error=str)
                            ranatest = str(spec)+str(test)

            if not ranatest:
                if args.specs:
                    specificnames = " named " + str(args.specs)
                else:
                    specificnames = ""
                tty.msg(" could not find any Pavilion tests named " + str(testnames) + \
                    " in installed specs" + specificnames)

def test_list(args):
    """List specs that have pavilion tests"""
   
    testnames = args.testnames
    
    env = ev.get_env(args, 'list')
    hashes = env.all_hashes() if env else None

    specs = spack.store.db.query_local(None, hashes=hashes)

    for spec in specs:
        tests = _get_pav_tests( spec )
        if tests:
            tests = [ os.path.basename( t ).split(".")[0] for t in tests ]
            # if any of names are in the spec
            # or any of names are in the individual tests
            if not testnames or any( n in str(spec) for n in testnames) or \
                any( n in t for t in tests for n in testnames):
                #TODO eventually something like spec.format("{name}-{version}-{hash:7}")
                #  instead of tty.msg(spec)
                tty.msg( spec )
                for t in tests:
                    if not testnames or any( n in str(spec) for n in testnames) or \
                        any( n in t for n in testnames):
                            tty.msg( " " + t )

def test_status(args):
    """Get the current status for a particular Spack test."""

    testnames = args.testnames
    if testnames:
        stage = get_stage(testnames[0])

    pav = _check_pavilion()

    if testnames and os.path.exists(stage):
        tty.msg("Test %s completed" % testnames)
    else:
        if pav:
            tests = pav('status', output=str, error=str).splitlines()[4:-1]
            if testnames:
                tests = [t for t in tests if any(n in t for n in testnames)]
            if tests:
                for t in tests:
                    t_ = t.split("|")
                    if t_[1].strip():
                        tty.msg("Test %s has status %s" % ( t_[1], t_[2] ))
            else:
                tty.msg("Tests %s not found" % testnames)
        else:
            tty.msg("Test %s no longer available" % testnames)

def test_results(args):
    """Get the results for a particular Spack test."""

    testnames = args.testnames
    if testnames:
        stage = get_stage(testnames[0])

    pav = _check_pavilion()

    # The results file may turn out to be a placeholder for future work
    if testnames and os.path.exists(stage):
        results_file = _get_results_file(testnames)
        if os.path.exists(results_file):
            msg = "Results for test %s: \n" % testnames
            with open(results_file, 'r') as f:
                lines = f.readlines()
            for line in lines:
                msg += "        %s" % line
            tty.msg(msg)
        else:
            msg = "Test %s has no results.\n" % testnames
            msg += "        Check if it is active with "
            msg += "`spack test status %s`" % testnames
            tty.msg(msg)
    else:
        if pav:
            tests = pav('results', output=str, error=str)
            tests = tests.splitlines()[4:-1]
            if testnames:
                tests = [t for t in tests if any(n in t for n in testnames)]
            if tests:
                for t in tests:
                    t_ = t.split("|")
                    tty.msg("Test %s has result %s" % ( t_[0], t_[2] ))
            else:
                tty.msg("No tests %s found" % testnames)
        else:
            tty.msg("No test %s found in test stage" % testnames)
# TODO eventually, testnames will be the metadata added from notes

def test_remove(args):
    """Remove results for a test from the test stage.

    If no test is listed, remove all tests from the test stage.

    Removed tests can no longer be accessed for results or status, and will not
    appear in `spack test list` results."""
    stage_dir = get_stage(args.name)
    if args.name:
        shutil.rmtree(stage_dir)
    else:
        fs.remove_directory_contents(stage_dir)


def test(parser, args):
    globals()['test_%s' % args.test_command](args)


def get_stage(name=None):
    """
    Return the test stage for the named test or the overall test stage.
    """
    stage_dir = spack.util.path.canonicalize_path(
        spack.config.get('config:test_stage', '~/.spack/test'))
    return os.path.join(stage_dir, name) if name else stage_dir

def _get_results_file(name):
    """
    Return the results file for the named test.
    """
    stage = get_stage(name)
    return os.path.join(stage, 'results.txt')
