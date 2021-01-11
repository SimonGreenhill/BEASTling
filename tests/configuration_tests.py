import io
import sys
from pathlib import Path
import logging

import pytest
import newick

import beastling
from beastling.configuration import Configuration, get_glottolog_data
from beastling.beastxml import BeastXml

pytestmark = pytest.mark.slow


def _processed_config(config_factory, *cfgs):
    config = config_factory(*cfgs)
    config.process()
    return config


def check_lat_lon(provided, target_lat, target_lon):
    prov_lat, prov_lon = provided
    return round(prov_lat, 2) == target_lat and round(prov_lon, 2) == target_lon


def test_partial_glottolog_coverage(config_factory):
    _processed_config(config_factory, 'admin', 'partial_glottolog_coverage')


def test_get_glottolog_geo(tmppath, mocker):
    geodata = tmppath / 'glottolog-2.5-geo.csv'
    geodata.write_text('x', encoding='utf8')

    mocker.patch(
        'beastling.configuration.user_data_dir',
        new=mocker.Mock(return_value=str(tmppath)))
    assert Path(get_glottolog_data('geo', '2.5')) == geodata


def test_get_glottolog_newick(tmppath, mocker):
    tmppath.joinpath('glottolog-2.5.newick').write_text(
        '(B [abcd1234],C [abcd1234])A [abcd1234];', encoding='utf8')
    mocker.patch(
        'beastling.configuration.user_data_dir',
        new=mocker.Mock(return_value=str(tmppath)))
    trees = newick.read(str(get_glottolog_data('newick', '2.5')))
    assert trees[0].name == 'A [abcd1234]'


def test_get_glottolog_data_download(tmppath, mocker):
    data_dir = tmppath / 'data'

    class URLopener(object):
        def retrieve(self, url, fname):
            with io.open(fname, 'w', encoding='utf8') as fp:
                fp.write('(B [abcd1234],C [abcd1234])A [abcd1234];')

    class URLopenerError(object):
        def retrieve(self, url, fname):
            raise IOError()

    mocker.patch('beastling.configuration.user_data_dir', mocker.Mock(return_value=str(data_dir)))
    mocker.patch('beastling.util.misc.URLopener', URLopenerError)
    with pytest.raises(ValueError):
        get_glottolog_data('newick', '2.5')

    mocker.patch('beastling.util.misc.URLopener', URLopener)
    assert get_glottolog_data('newick', '2.5')


def test_families(config_factory):
    cfg1 = _processed_config(config_factory, 'glottolog_families')
    cfg2 = _processed_config(config_factory, 'glottolog_families_from_file')
    assert cfg1.languages.languages == cfg2.languages.languages


def test_loading_of_dialect_data(config_factory):
    cfg = _processed_config(config_factory, 'basic', 'geo')
    assert '3adt1234' in cfg.locations


def test_config(config_dir):
    cfg = Configuration(configfile={
        'admin': {

        },
        'languages': {
            'monophyly': True,
            'starting_tree': '(T)',
            'sample_topology': False,
            'sample_branch_lengths': False,
        },
        'calibration': {
            'abcd1234, efgh5678': '10-20',
        },
        'model one': {
            'binarised': True,
            'minimum_data': '4.5'
        },
        'model two': {
            'binarized': True,
        },
    })
    assert cfg.calibration_configs['abcd1234, efgh5678'] == "10-20"
    assert cfg.models[1].binarised

    with pytest.raises(ValueError, match="'overlap' must be"):
        Configuration(configfile={'languages': {'overlap': 'invalid'}, 'models T': {'model': 'mk'}})

    with pytest.raises(ValueError, match='Config file'):
        Configuration(configfile={'languages': {}})

    # Test filename as string:
    cfg = Configuration(configfile=str(config_dir / 'basic.conf'))
    assert cfg.mcmc.chainlength == 10


def test_monophyly_with_unknown_language(config_factory, data_dir, caplog):
    datafile = data_dir / 'basic.csv'
    data = datafile.read_text(encoding='utf8').strip()
    data += '\nxyzz,1,1,1,1,1,1,1,1,1,1\n'
    datafile.write_text(data, encoding='utf8')
    cfg = config_factory('basic', 'monophyletic')
    cfg.process()
    assert any('Monophyly constraints' in rec.message for rec in caplog.records)


def test_disabling_monophyly(config_factory, data_dir, caplog):
    datafile = data_dir / 'basic.csv'
    data = datafile.read_text(encoding='utf8').split('\n')
    datafile.write_text('\n'.join(data[:3]), encoding='utf8')
    cfg = config_factory('basic', 'monophyletic')
    with caplog.at_level(logging.INFO, logger=beastling.__name__):
        cfg.process()
        assert any('Disabling Glottolog' in rec.message for rec in caplog.records)


def test_multiple_processing(config_factory, config_dir, caplog):
    cfg = config_factory('basic', 'geo')
    cfg.process()
    # processing again doesn't do anything!
    cfg.process()
    assert 'already' in caplog.records[-1].message
    cfg.load_glottolog_data()
    message = caplog.records[-1].message
    assert ('already' in message) and ('Glottolog' in message)


@pytest.mark.parametrize(
    'cfg',
    [
        "no_data",
        "no_langs",
        "no_model_sec",
        "no_model",
        "unknown_model",
        "bad_overlap",
        "cal_originate_root",
        "bad_wrong_tree_filename",
        "bad_share_params",
        "bad_treeprior",
        ["basic", "bad_cal_endpoints"],
        ["basic", "monophyletic", "bad_cal_monophyly"],
        "misspelled_clock",
        ["basic", "geo_prior"],  # geo priors, but no geography!
    ]
)
def test_invalid_config(cfg, config_factory):
    with pytest.raises(ValueError):
        cfg_ = config_factory(*cfg) if isinstance(cfg, list) else config_factory(cfg)
        cfg_.process()


def test_bad_frequencies(config_factory):
    cfg = _processed_config(config_factory, "bad_frequencies")
    with pytest.raises(ValueError):
        # This is an error in a model section, which is only raised
        # when the model is constructed as XML.
        BeastXml(cfg)


@pytest.mark.parametrize(
    'cfg,dist,assertion',
    [
        ('calibration_lower_bound', 'uniform', lambda v: v.param[1] == sys.maxsize),
        ('calibration_upper_bound', 'uniform', lambda v: v.param[0] == 0.0),
        ('calibration_normal_range', 'normal', None),
        ('calibration_normal_params', 'normal', None),
        ('calibration_lognormal_range', 'lognormal', None),
        ('calibration_lognormal_params', 'lognormal', None),
        ('calibration_uniform_range', 'uniform', None),
        ('calibration_uniform_params', 'uniform', None),
    ]
)
def test_calibration_string_formats(config_factory, cfg, dist, assertion):
    config = _processed_config(config_factory, 'basic', cfg)
    assert list(config.calibrations.values())[0].dist == dist
    if assertion:
        assert assertion(list(config.calibrations.values())[0])


def test_minimum_data(config_factory):
    # f8 has 60% missing data.  By default it should be included...
    config = _processed_config(config_factory, 'basic')
    assert "f8" in config.models[0].features
    # ...but if we insist on 75% data or more it should be excluded...
    config = _processed_config(config_factory, 'basic', 'minimum_data')
    assert config.models[0].minimum_data == 75.0
    assert "f8" not in config.models[0].features


def test_pruned_rlc(config_factory):
    # Make sure pruned trees are disabled if used in conjunction with RLC
    config = config_factory('basic', 'pruned', 'random')
    assert config.models[0].pruned
    config.process()
    assert not config.models[0].pruned


def test_no_monophyly_geo(config_factory, caplog):
    # Make sure that geographic sampling without monophyly constraints emits a warning
    _processed_config(config_factory, 'basic', 'geo', 'geo_sampled')
    assert any("Geographic sampling" in r.message for r in caplog.records)


def test_ascertainment_auto_setting(config_factory):
    # Without calibration, there should be no ascertainment...
    config = _processed_config(config_factory, 'basic')
    assert not config.models[0].ascertained
    # But with it there should...
    config = _processed_config(config_factory, 'basic', 'calibration')
    assert config.models[0].ascertained
    # Unless, of course, we have constant data...
    config = config_factory('covarion_multistate', 'calibration')
    config.models[0].remove_constant_features = False
    config.process()
    assert not config.models[0].ascertained


def test_ascertainment_override(config_factory):
    # Make sure we can override the above automagic
    config = _processed_config(config_factory, 'basic', 'ascertainment_true')
    assert config.models[0].ascertained
    # And with calibrations...
    config = _processed_config(config_factory, 'basic', 'calibration', 'ascertainment_false')
    assert not config.models[0].ascertained


def test_bad_ascertainment(config_factory):
    # Make sure we refuse to produce a misspecified model
    config = config_factory('covarion_multistate', 'ascertainment_true')
    config.models[0].remove_constant_features = False
    with pytest.raises(ValueError):
        config.process()


def test_user_locations(config_factory):
    # First check that we correctly load Glottolog's locations for aiw and abp
    config = _processed_config(config_factory, 'basic', 'geo')
    assert check_lat_lon(config.locations["aiw"], 5.95, 36.57)
    assert check_lat_lon(config.locations["abp"], 15.41, 120.20)
    # Now check that we can overwrite just one of these...
    config = _processed_config(config_factory, 'basic', 'geo', 'geo_user_loc')
    assert check_lat_lon(config.locations["aiw"], 4.20, 4.20)
    assert check_lat_lon(config.locations["abp"], 15.41, 120.20)
    # Now check that we can overwrite them both using multiple files
    config = _processed_config(config_factory, 'basic', 'geo', 'geo_user_loc_multifile')
    assert check_lat_lon(config.locations["aiw"], 4.20, 4.20)
    assert check_lat_lon(config.locations["abp"], 6.66, 6.66)


def test_monophyly_levels(config_factory):
    # The isolates.csv data file contains Japanese, Korean and Basque, plus
    # English and Russian.  When used with standard monophly, we should see
    # a four-way polytomy with eng+rus grouped (IE) and the rest isolated.
    config = _processed_config(config_factory, 'admin', 'mk', 'isolates', 'monophyletic')
    tree = newick.loads(config.languages.monophyly_newick)[0]
    assert len(tree.descendants) == 4
    for node in tree.descendants:
        if len(node.descendants) == 2:
            assert all((l.is_leaf and l.name in ("eng", "rus") for l in node.descendants))
    # Now we set monophyly_start_depth to 1, i.e. ignore the top-most
    # level of Glottolog constraints.  Now we should just have a massive
    # polytomy, since IE no longer matters and eng and rus are in separate
    # subfamilies (Germanic vs Balto-Slavic).
    config = _processed_config(config_factory, 'admin', 'mk', 'isolates', 'monophyletic-start-depth')
    tree = newick.loads(config.languages.monophyly_newick)[0]
    assert len(tree.descendants) == 5


def test_subsampling(config_factory):
    # First check how many languages there usually are
    config = _processed_config(config_factory, 'admin', 'mk')
    full_lang_count = len(config.languages.languages)
    # Try various subsamples and make sure they work
    for subsample_size in range(2, full_lang_count):
        config = config_factory('admin', 'mk')
        config.languages.subsample_size = subsample_size
        config.process()
        assert len(config.languages.languages) == subsample_size
    # Make sure if we ask for more languages than we have nothing happens
    config = config_factory('admin', 'mk')
    config.languages.subsample_size = full_lang_count + 42
    config.process()
    assert len(config.languages.languages) == full_lang_count


def test_language_groups(config_factory):
    config = _processed_config(config_factory, 'basic', 'taxa')
    assert config.language_groups["abf"] == {"abf"}
    assert config.language_groups["macronesian"] == {"kbt", "abf", "abg"}


def test_nonexisting_language_group(config_factory):
    config = config_factory('basic', 'reconstruct_one')
    with pytest.raises(KeyError):
        config.process()


def test_explicit_strict_clock(config_factory):
    _ = _processed_config(config_factory, 'basic', 'strict')


def test_calibration(config_factory):
    config = _processed_config(config_factory, 'basic', 'calibration')
    assert {'Cushitic'} == set(config.calibrations)
    v = config.calibrations['Cushitic']
    assert 'DistributionForCushiticMRCA' in BeastXml(config).tostring().decode('utf8')

    # Now remove one calibration point ...
    del config.calibrations['Cushitic']
    assert 'DistributionForCushiticMRCA' not in BeastXml(config).tostring().decode('utf8')

    # ... and add it back in with using the glottocode:
    config.calibrations['cush1243'] = v
    assert 'DistributionForcush1243MRCA' in BeastXml(config).tostring().decode('utf8')


@pytest.mark.parametrize(
    'cfgs,in_xml',
    [
        (
                ['basic', 'calibration_tip_offset'],
                ['aal = 40.0']),
        (
                ['covarion_binarised', 'ascertainment_false'],
                ['ascertained="true"', 'excludeto="1"']),
        (
                ['glottolog_families_from_file', 'embed_data'],
                ["aari1239,1,1,1,1,1,1,?,1,?,1", "Malayo-Polynesian"]),
    ]
)
def test_xml(config_factory, cfgs, in_xml):
    config = _processed_config(config_factory, *cfgs)
    xml = BeastXml(config).tostring().decode('utf8')
    for s in in_xml:
        assert s in xml, s
