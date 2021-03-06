# Copyright 2013-2019 Lawrence Livermore National Security, LLC and other
# Spack Project Developers. See the top-level COPYRIGHT file for details.
#
# SPDX-License-Identifier: (Apache-2.0 OR MIT)

"""
This test checks creating and install buildcaches
"""
import os
import sys
import py
import pytest
import argparse
import platform
import spack.repo
import spack.store
import spack.binary_distribution as bindist
import spack.cmd.buildcache as buildcache
import spack.cmd.install as install
import spack.cmd.uninstall as uninstall
import spack.cmd.mirror as mirror
from spack.spec import Spec
from spack.directory_layout import YamlDirectoryLayout


def_install_path_scheme = '${ARCHITECTURE}/${COMPILERNAME}-${COMPILERVER}/${PACKAGE}-${VERSION}-${HASH}'  # noqa: E501
ndef_install_path_scheme = '${PACKAGE}/${VERSION}/${ARCHITECTURE}-${COMPILERNAME}-${COMPILERVER}-${HASH}'  # noqa: E501

mirror_path_def = None
mirror_path_rel = None


@pytest.fixture(scope='function')
def cache_directory(tmpdir):
    old_cache_path = spack.caches.fetch_cache
    tmpdir.ensure('fetch_cache', dir=True)
    fsc = spack.fetch_strategy.FsCache(str(tmpdir.join('fetch_cache')))
    spack.config.caches = fsc
    yield spack.config.caches
    tmpdir.join('fetch_cache').remove()
    spack.config.caches = old_cache_path


@pytest.fixture(scope='session')
def session_mirror_def(tmpdir_factory):
    dir = tmpdir_factory.mktemp('mirror')
    global mirror_path_rel
    mirror_path_rel = dir
    dir.ensure('build_cache', dir=True)
    yield dir
    dir.join('build_cache').remove()


@pytest.fixture(scope='function')
def mirror_directory_def(session_mirror_def):
    yield str(session_mirror_def)


@pytest.fixture(scope='session')
def session_mirror_rel(tmpdir_factory):
    dir = tmpdir_factory.mktemp('mirror')
    global mirror_path_rel
    mirror_path_rel = dir
    dir.ensure('build_cache', dir=True)
    yield dir
    dir.join('build_cache').remove()


@pytest.fixture(scope='function')
def mirror_directory_rel(session_mirror_rel):
    yield(session_mirror_rel)


@pytest.fixture(scope='session')
def config_directory(tmpdir_factory):
    tmpdir = tmpdir_factory.mktemp('test_configs')
    # restore some sane defaults for packages and config
    config_path = py.path.local(spack.paths.etc_path)
    modules_yaml = config_path.join('spack', 'defaults', 'modules.yaml')
    os_modules_yaml = config_path.join('spack', 'defaults', '%s' %
                                       platform.system().lower(),
                                       'modules.yaml')
    packages_yaml = config_path.join('spack', 'defaults', 'packages.yaml')
    config_yaml = config_path.join('spack', 'defaults', 'config.yaml')
    repos_yaml = config_path.join('spack', 'defaults', 'repos.yaml')
    tmpdir.ensure('site', dir=True)
    tmpdir.ensure('user', dir=True)
    tmpdir.ensure('site/%s' % platform.system().lower(), dir=True)
    modules_yaml.copy(tmpdir.join('site', 'modules.yaml'))
    os_modules_yaml.copy(tmpdir.join('site/%s' % platform.system().lower(),
                                     'modules.yaml'))
    packages_yaml.copy(tmpdir.join('site', 'packages.yaml'))
    config_yaml.copy(tmpdir.join('site', 'config.yaml'))
    repos_yaml.copy(tmpdir.join('site', 'repos.yaml'))
    yield tmpdir
    tmpdir.remove()


@pytest.fixture(scope='function')
def default_config(tmpdir_factory, config_directory, monkeypatch):

    mutable_dir = tmpdir_factory.mktemp('mutable_config').join('tmp')
    config_directory.copy(mutable_dir)

    cfg = spack.config.Configuration(
        *[spack.config.ConfigScope(name, str(mutable_dir))
          for name in ['site/%s' % platform.system().lower(),
                       'site', 'user']])

    monkeypatch.setattr(spack.config, 'config', cfg)

    # This is essential, otherwise the cache will create weird side effects
    # that will compromise subsequent tests if compilers.yaml is modified
    monkeypatch.setattr(spack.compilers, '_cache_config_file', [])
    njobs = spack.config.get('config:build_jobs')
    if not njobs:
        spack.config.set('config:build_jobs', 4, scope='user')
    extensions = spack.config.get('config:template_dirs')
    if not extensions:
        spack.config.set('config:template_dirs',
                         [os.path.join(spack.paths.share_path, 'templates')],
                         scope='user')

    mutable_dir.ensure('build_stage', dir=True)
    build_stage = spack.config.get('config:build_stage')
    if not build_stage:
        spack.config.set('config:build_stage',
                         [str(mutable_dir.join('build_stage'))], scope='user')
    timeout = spack.config.get('config:connect_timeout')
    if not timeout:
        spack.config.set('config:connect_timeout', 10, scope='user')
    yield spack.config.config
    mutable_dir.remove()


@pytest.fixture(scope='function')
def install_dir_default_layout(tmpdir):
    """Hooks a fake install directory with a default layout"""
    real_store = spack.store.store
    real_layout = spack.store.layout
    spack.store.store = spack.store.Store(str(tmpdir.join('opt')))
    spack.store.layout = YamlDirectoryLayout(str(tmpdir.join('opt')),
                                             path_scheme=def_install_path_scheme)  # noqa: E501
    yield spack.store
    spack.store.store = real_store
    spack.store.layout = real_layout


@pytest.fixture(scope='function')
def install_dir_non_default_layout(tmpdir):
    """Hooks a fake install directory with a non-default layout"""
    real_store = spack.store.store
    real_layout = spack.store.layout
    spack.store.store = spack.store.Store(str(tmpdir.join('opt')))
    spack.store.layout = YamlDirectoryLayout(str(tmpdir.join('opt')),
                                             path_scheme=ndef_install_path_scheme)  # noqa: E501
    yield spack.store
    spack.store.store = real_store
    spack.store.layout = real_layout


args = ['strings', 'file']
if sys.platform == 'darwin':
    args.extend(['/usr/bin/clang++', 'install_name_tool'])
else:
    args.extend(['/usr/bin/g++', 'patchelf'])


@pytest.mark.requires_executables(*args)
@pytest.mark.disable_clean_stage_check
@pytest.mark.maybeslow
@pytest.mark.usefixtures('default_config', 'cache_directory',
                         'install_dir_default_layout')
def test_default_rpaths_create_install_default_layout(tmpdir,
                                                      mirror_directory_def,
                                                      install_mockery):
    """
    Test the creation and installation of buildcaches with default rpaths
    into the default directory layout scheme.
    """

    gspec = Spec('garply')
    gspec.concretize()
    cspec = Spec('corge')
    cspec.concretize()

    iparser = argparse.ArgumentParser()
    install.setup_parser(iparser)
    # Install some packages with dependent packages
    iargs = iparser.parse_args(['--no-cache', cspec.name])
    install.install(iparser, iargs)

    global mirror_path_def
    mirror_path_def = mirror_directory_def
    mparser = argparse.ArgumentParser()
    mirror.setup_parser(mparser)
    margs = mparser.parse_args(
        ['add', '--scope', 'site', 'test-mirror-def', 'file://%s' % mirror_path_def])
    mirror.mirror(mparser, margs)
    margs = mparser.parse_args(['list'])
    mirror.mirror(mparser, margs)

    # setup argument parser
    parser = argparse.ArgumentParser()
    buildcache.setup_parser(parser)

    # Set default buildcache args
    create_args = ['create', '-a', '-u', '-d', str(mirror_path_def),
                   cspec.name]
    install_args = ['install', '-a', '-u', cspec.name]

    # Create a buildache
    args = parser.parse_args(create_args)
    buildcache.buildcache(parser, args)
    # Test force overwrite create buildcache
    create_args.insert(create_args.index('-a'), '-f')
    args = parser.parse_args(create_args)
    buildcache.buildcache(parser, args)
    # create mirror index
    args = parser.parse_args(['update-index', '-d', 'file://%s' % str(mirror_path_def)])
    buildcache.buildcache(parser, args)
    # list the buildcaches in the mirror
    args = parser.parse_args(['list', '-a', '-l', '-v'])
    buildcache.buildcache(parser, args)

    # Uninstall the package and deps
    uparser = argparse.ArgumentParser()
    uninstall.setup_parser(uparser)
    uargs = uparser.parse_args(['-y', '--dependents', gspec.name])
    uninstall.uninstall(uparser, uargs)

    # test install
    args = parser.parse_args(install_args)
    buildcache.buildcache(parser, args)

    # This gives warning that spec is already installed
    buildcache.buildcache(parser, args)

    # test overwrite install
    install_args.insert(install_args.index('-a'), '-f')
    args = parser.parse_args(install_args)
    buildcache.buildcache(parser, args)

    args = parser.parse_args(['keys', '-f'])
    buildcache.buildcache(parser, args)

    args = parser.parse_args(['list'])
    buildcache.buildcache(parser, args)

    args = parser.parse_args(['list', '-a'])
    buildcache.buildcache(parser, args)

    args = parser.parse_args(['list', '-l', '-v'])
    buildcache.buildcache(parser, args)
    bindist._cached_specs = set()
    spack.stage.purge()
    margs = mparser.parse_args(
        ['rm', '--scope', 'site', 'test-mirror-def'])
    mirror.mirror(mparser, margs)


@pytest.mark.requires_executables(*args)
@pytest.mark.disable_clean_stage_check
@pytest.mark.maybeslow
@pytest.mark.nomockstage
@pytest.mark.usefixtures('default_config', 'cache_directory',
                         'install_dir_non_default_layout')
def test_default_rpaths_install_nondefault_layout(tmpdir,
                                                  install_mockery):
    """
    Test the creation and installation of buildcaches with default rpaths
    into the non-default directory layout scheme.
    """

    gspec = Spec('garply')
    gspec.concretize()
    cspec = Spec('corge')
    cspec.concretize()

    global mirror_path_def
    mparser = argparse.ArgumentParser()
    mirror.setup_parser(mparser)
    margs = mparser.parse_args(
        ['add', '--scope', 'site', 'test-mirror-def', 'file://%s' % mirror_path_def])
    mirror.mirror(mparser, margs)

    # setup argument parser
    parser = argparse.ArgumentParser()
    buildcache.setup_parser(parser)

    # Set default buildcache args
    install_args = ['install', '-a', '-u', '%s' % cspec.name]

    # Install some packages with dependent packages
    # test install in non-default install path scheme
    args = parser.parse_args(install_args)
    buildcache.buildcache(parser, args)
    # test force install in non-default install path scheme
    install_args.insert(install_args.index('-a'), '-f')
    args = parser.parse_args(install_args)
    buildcache.buildcache(parser, args)

    bindist._cached_specs = set()
    spack.stage.purge()
    margs = mparser.parse_args(
        ['rm', '--scope', 'site', 'test-mirror-def'])
    mirror.mirror(mparser, margs)


@pytest.mark.requires_executables(*args)
@pytest.mark.disable_clean_stage_check
@pytest.mark.maybeslow
@pytest.mark.nomockstage
@pytest.mark.usefixtures('default_config', 'cache_directory',
                         'install_dir_default_layout')
def test_relative_rpaths_create_default_layout(tmpdir,
                                               mirror_directory_rel,
                                               install_mockery):
    """
    Test the creation and installation of buildcaches with relative
    rpaths into the default directory layout scheme.
    """

    gspec = Spec('garply')
    gspec.concretize()
    cspec = Spec('corge')
    cspec.concretize()

    global mirror_path_rel
    mirror_path_rel = mirror_directory_rel
    # Install patchelf needed for relocate in linux test environment
    iparser = argparse.ArgumentParser()
    install.setup_parser(iparser)
    # Install some packages with dependent packages
    iargs = iparser.parse_args(['--no-cache', cspec.name])
    install.install(iparser, iargs)

    # setup argument parser
    parser = argparse.ArgumentParser()
    buildcache.setup_parser(parser)

    # set default buildcache args
    create_args = ['create', '-a', '-u', '-r', '-d',
                   str(mirror_path_rel),
                   cspec.name]

    # create build cache with relatived rpaths
    args = parser.parse_args(create_args)
    buildcache.buildcache(parser, args)
    # create mirror index
    args = parser.parse_args(['update-index', '-d', 'file://%s' % str(mirror_path_rel)])
    buildcache.buildcache(parser, args)
    # Uninstall the package and deps
    uparser = argparse.ArgumentParser()
    uninstall.setup_parser(uparser)
    uargs = uparser.parse_args(['-y', '--dependents', gspec.name])
    uninstall.uninstall(uparser, uargs)

    bindist._cached_specs = set()
    spack.stage.purge()


@pytest.mark.requires_executables(*args)
@pytest.mark.disable_clean_stage_check
@pytest.mark.maybeslow
@pytest.mark.nomockstage
@pytest.mark.usefixtures('default_config', 'cache_directory',
                         'install_dir_default_layout')
def test_relative_rpaths_install_default_layout(tmpdir,
                                                install_mockery):
    """
    Test the creation and installation of buildcaches with relative
    rpaths into the default directory layout scheme.
    """

    gspec = Spec('garply')
    gspec.concretize()
    cspec = Spec('corge')
    cspec.concretize()

    global mirror_path_rel
    mparser = argparse.ArgumentParser()
    mirror.setup_parser(mparser)
    margs = mparser.parse_args(
        ['add', '--scope', 'site', 'test-mirror-rel', 'file://%s' % mirror_path_rel])
    mirror.mirror(mparser, margs)

    iparser = argparse.ArgumentParser()
    install.setup_parser(iparser)

    # setup argument parser
    parser = argparse.ArgumentParser()
    buildcache.setup_parser(parser)

    # set default buildcache args
    install_args = ['install', '-a', '-u',
                    cspec.name]

    # install buildcache created with relativized rpaths
    args = parser.parse_args(install_args)
    buildcache.buildcache(parser, args)

    # This gives warning that spec is already installed
    buildcache.buildcache(parser, args)

    # Uninstall the package and deps
    uparser = argparse.ArgumentParser()
    uninstall.setup_parser(uparser)
    uargs = uparser.parse_args(['-y', '--dependents', gspec.name])
    uninstall.uninstall(uparser, uargs)

    # install build cache
    buildcache.buildcache(parser, args)

    # test overwrite install
    install_args.insert(install_args.index('-a'), '-f')
    args = parser.parse_args(install_args)
    buildcache.buildcache(parser, args)

    bindist._cached_specs = set()
    spack.stage.purge()
    margs = mparser.parse_args(
        ['rm', '--scope', 'site', 'test-mirror-rel'])
    mirror.mirror(mparser, margs)


@pytest.mark.requires_executables(*args)
@pytest.mark.disable_clean_stage_check
@pytest.mark.maybeslow
@pytest.mark.nomockstage
@pytest.mark.usefixtures('default_config', 'cache_directory',
                         'install_dir_non_default_layout')
def test_relative_rpaths_install_nondefault(tmpdir,
                                            install_mockery):
    """
    Test the installation of buildcaches with relativized rpaths
    into the non-default directory layout scheme.
    """

    gspec = Spec('garply')
    gspec.concretize()
    cspec = Spec('corge')
    cspec.concretize()

    global mirror_path_rel

    mparser = argparse.ArgumentParser()
    mirror.setup_parser(mparser)
    margs = mparser.parse_args(
        ['add', '--scope', 'site', 'test-mirror-rel', 'file://%s' % mirror_path_rel])
    mirror.mirror(mparser, margs)

    iparser = argparse.ArgumentParser()
    install.setup_parser(iparser)

    # setup argument parser
    parser = argparse.ArgumentParser()
    buildcache.setup_parser(parser)

    # Set default buildcache args
    install_args = ['install', '-a', '-u', '%s' % cspec.name]

    # test install in non-default install path scheme and relative path
    args = parser.parse_args(install_args)
    buildcache.buildcache(parser, args)

    bindist._cached_specs = set()
    spack.stage.purge()
    margs = mparser.parse_args(
        ['rm', '--scope', 'site', 'test-mirror-rel'])
    mirror.mirror(mparser, margs)
