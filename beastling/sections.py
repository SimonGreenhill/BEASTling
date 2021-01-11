import sys
from configparser import ConfigParser
import pathlib
import collections
import functools
import importlib

import attr

from beastling.util.misc import sanitise_tree, all_subclasses
from beastling.util import log
from beastling.clocks import *  # Make sure all clocks are imported.
from beastling.clocks.baseclock import BaseClock
from beastling.models import *  # Make sure all models are imported.
from beastling.models.basemodel import BaseModel

__all__ = ['Admin', 'MCMC', 'Languages']

_BEAST_MAX_LENGTH = 2147483647

ConfigValue = collections.namedtuple('ConfigValue', ['value', 'fname'])


def _to_cfg(v):
    if isinstance(v, (list, set, tuple)):
        return '\n'.join(str(vv) for vv in v)
    return str(v)


@attr.s
class Section(object):
    __allow_arbitrary_options__ = False

    name = attr.ib()  # We keep the section name ...
    cli_params = attr.ib(default=attr.Factory(dict))  # ... and the parameters passed on the cli.
    # We also need to keep track of all the data loaded from extra files:
    files_to_embed = attr.ib(default=attr.Factory(set))
    options = attr.ib(default=attr.Factory(collections.OrderedDict))

    @classmethod
    def from_config(cls, cli_params, section, cfg):
        """
        Initialize a Section object from a ConfigParser section.

        :param cli_params: `dict` of parameters passed on the command line
        :param section: The name of the section.
        :param cfg: The `ConfigParser` instance.
        :return: Initialized `cls` instance.
        """
        kw, files_to_embed = {'options': collections.OrderedDict()}, set()
        fields = attr.fields(cls)
        for field in fields:
            if field.name not in ['name', 'cli_params', 'files_to_embed']:
                opt = field.name
                if opt.endswith('_'):
                    opt = opt[:-1]
                if (section in cfg) and (opt in cfg[section]):
                    method = field.metadata.get('getter', ConfigParser.get)
                    res = method(cfg, section, opt)
                    if isinstance(res, ConfigValue):
                        kw[field.name] = res.value
                        files_to_embed.add(res.fname)
                    else:
                        kw[field.name] = res
                    # Update the ConfigParser because we use this object to embed the config in
                    # a BEAST XML comment:
                    cfg[section][opt] = field.metadata.get('setter', _to_cfg)(kw[field.name])
        if section in cfg:
            opts = [f.name[:-1] if f.name.endswith('_') else f.name for f in fields]
            for opt in cfg[section]:
                if opt not in opts:
                    if cls.__allow_arbitrary_options__:
                        kw['options'][opt] = cfg.get(section, opt)
                    else:
                        raise ValueError("Unrecognised option %s in section %s!" % (opt, section))
        return cls(name=section, cli_params=cli_params, files_to_embed=files_to_embed, **kw)


def opt(default, help=None, getter=ConfigParser.get, setter=_to_cfg, **kw):
    """
    Wrap `attr.ib` to add syntactic sugar for creation of `metadata`.

    :param default: default value for the attribute
    :param help: help string, documenting the usage of the attribute
    :param getter: callable to use to extract a value from a `ConfigParser` instance
    :param kw: additional keyword arguments to pass into `attr.ib`
    :return: an attribute instance
    """
    return attr.ib(default, metadata=dict(help=help, getter=getter, setter=setter), **kw)


def get_list_of_files(cfg, section, option):
    return [pathlib.Path(f.strip()) for f in cfg.get(section, option).split(',') if f.strip()]


def get_file_or_list(cfg, section, option):
    """
    Retrieves a list-valued config option from a string or a file.

    :param cfg: `ConfigParser` instance
    :param section: section name
    :param option: option name
    :return: `list`of values for the option
    """
    return handle_file_or_list(cfg.get(section, option))


def handle_file_or_list(value):
    """
    Provides the functionality formerly available as `Configuration.handle_file_or_list`.
    """
    if not isinstance(value, (list, tuple, set)):
        fname = pathlib.Path(value)
        if fname.exists():
            with fname.open() as fid:
                return ConfigValue([line.strip() for line in fid], fname)
        else:
            return [x.strip() for x in value.split(",")]
    return value


def get_tree(tree_type, cfg, section, option):
    """Load a tree from file or parse a string.

    If the provided value is the name of an existing file, read the contents and treat it as a
    Newick tree specification. Otherwise, assume the provided value is a Newick tree specification.

    Trees consisting of only one leaf are considered errors, because they are never useful and can
    easily arise when a non-existing file name is parsed as tree, leading to confusing error
    messages down the line.

    In either case, inspect the tree and make appropriate minor changes so it is suitable for
    inclusion in the BEAST XML file.
    """
    value = cfg.get(section, option)
    assert tree_type in ("starting", "monophyly")
    # Read from file if necessary
    fname = pathlib.Path(value)
    if fname.exists() and fname.is_file():
        value = fname.read_text(encoding='utf8').strip()
    if value:
        if ")" in value:
            return value
        # A tree with only one node (which is the only Newick string without bracket) is not a
        # useful tree specification.
        raise ValueError(
            "Tree specification {:} is neither an existing file nor does it look "
            "like a useful tree.".format(value))
    return value


@attr.s
class Admin(Section):
    basename_ = opt(
        'beastling',
        "Used as a common prefix for output filenames (e.g. the log will be called basename.log).",
        validator=attr.validators.instance_of(str))
    embed_data = opt(
        False,
        "A boolean value, controlling whether or not to embed data files in the XML.",
        getter=ConfigParser.getboolean)
    screenlog = opt(
        True,
        "A boolean parameter, controlling whether or not to log some basic output to stdout.",
        getter=ConfigParser.getboolean)
    log_all = opt(
        False,
        "A boolean value, setting this True is a shortcut for setting log_params, "
        "log_probabilities, log_fine_probs and log_trees True.",
        getter=ConfigParser.getboolean)
    log_dp = opt(
        4,
        "An integer value, setting the number of decimal points to use when logging rates, "
        "locations, etc.  Defaults to 4.  Use -1 to enable full precision.",
        getter=ConfigParser.getint)
    log_every = opt(
        0,
        "An integer indicating how many MCMC iterations should occurr between consecutive log "
        "entries.",
        getter=ConfigParser.getint)
    log_probabilities = opt(
        True,
        "A boolean value, controlling whether or not to log the prior, likelihood and posterior "
        "of the analysis.",
        getter=ConfigParser.getboolean)
    log_fine_probs = opt(
        False,
        "A boolean value, controlling whether or not to log individual components of the prior "
        "and likelihood.  Setting this True automatically sets log_probabilities True.",
        getter=ConfigParser.getboolean)
    log_params = opt(
        False,
        "A boolean value, controlling whether or not to log model parameters.",
        getter=ConfigParser.getboolean)
    log_trees = opt(
        True,
        "A boolean value, controlling whether or not to log the sampled trees.",
        getter=ConfigParser.getboolean)
    log_pure_tree = opt(
        False,
        "A boolean value, controlling whether or not to log a separate file of the sampled "
        "trees with no metadata included.",
        getter=ConfigParser.getboolean)
    glottolog_release = opt(
        "4.0",
        "A string representing a Glottolog release number.",
        getter=ConfigParser.get)

    def __attrs_post_init__(self):
        if self.log_all:
            self.log_trees = self.log_params = self.log_probabilities = self.log_fine_probs = True
        if self.log_fine_probs:
            self.log_probabilities = True

    @property
    def basename(self):
        return '{0}_prior'.format(self.basename_) \
            if self.cli_params.get('prior') else self.basename_

    def path(self, suffix):
        return pathlib.Path(self.basename + suffix)


@attr.s
class MCMC(Section):
    alpha = opt(
        0.3,
        "Alpha parameter for path sampling intervals.",
        getter=ConfigParser.getfloat)
    chainlength = opt(
        10000000,
        "Number of iterations to run the Markov chain for.",
        getter=ConfigParser.getint)
    do_not_run = opt(
        False,
        "A boolean value, controlling whether or not BEAST should run path sampling analyses or "
        "just generate the file and scripts to do so.",
        getter=ConfigParser.getboolean)
    log_burnin = opt(
        50,
        "Proportion of logs to discard as burnin when calculating marginal likelihood from path "
        "sampling.",
        getter=ConfigParser.getint)
    path_sampling = opt(
        False,
        "A boolean value, controlling whether to do a standard MCMC run or a Path Sampling "
        "analysis for marginal likelihood estimation.",
        getter=ConfigParser.getboolean)
    preburnin = opt(
        10,
        "Percentage of chainlength to discard as burnin for the first step in a path sampling "
        "analysis.",
        getter=ConfigParser.getint)
    sample_from_prior = opt(
        False,
        "Boolean parameter; if True, data is ignored and the MCMC chain will sample from the prior.",
        getter=ConfigParser.getboolean)
    steps = opt(
        8,
        "Number of steps between prior and posterior in path sampling analysis.",
        getter=ConfigParser.getint)

    def __attrs_post_init__(self):
        if self.chainlength > _BEAST_MAX_LENGTH:
            self.chainlength = _BEAST_MAX_LENGTH
            log.info("Chain length truncated to {0}, as BEAST cannot handle longer chains.".format(
                _BEAST_MAX_LENGTH))
        if bool(self.cli_params.get('prior')) and self.path_sampling:
            raise ValueError("Cannot sample from the prior during a path sampling analysis.")
        self.sample_from_prior = bool(self.cli_params.get('prior')) or self.sample_from_prior


@attr.s
class Languages(Section):
    exclusions = opt(
        attr.Factory(list),
        "A list of languages to exclude from the analysis, or a name of a file containing such a list.",
        getter=get_file_or_list)
    languages = opt(
        attr.Factory(list),
        "List of languages to filter down to, or name of a file containing such a list.",
        getter=get_file_or_list)
    families = opt(
        attr.Factory(list),
        "List of families to filter down to, or name of a file containing such a list.",
        getter=get_file_or_list)
    macroareas = opt(
        attr.Factory(list),
        "List of Glottolog macro-areas to filter down to, or name of a file containing such a list.",
        getter=get_file_or_list)
    overlap = opt(
        "union",
        "Either the string 'union' or the string 'intersection', controlling how to handle "
        "multiple datasets with non-equal language sets.",
        validator=attr.validators.in_(['union', 'intersection']),
        converter=lambda s: s.lower(),
    )
    starting_tree = opt(
        None,
        "A starting tree in Newick format, or the name of a file containing the same.",
        getter=functools.partial(get_tree, 'starting'))
    sample_branch_lengths = opt(
        True,
        "A boolean value, controlling whether or not to estimate tree branch lengths.",
        getter=ConfigParser.getboolean)
    sample_topology = opt(
        True,
        "A boolean value, controlling whether or not to estimate tree topology.",
        getter=ConfigParser.getboolean)
    subsample_size = opt(
        0,
        'Number of languages to subsample from the set defined by the dataset(s) and other '
        'filtering options like "families" or "macroareas".',
        getter=ConfigParser.getint)
    minimum_data = opt(
        0.0,
        "A floating point value, indicated the percentage of datapoints, across ALL models, which "
        "a language must have in order to be included in the analysis.",
        getter=ConfigParser.getfloat)
    monophyly = opt(
        False,
        "A boolean parameter, controlling whether or not to enforce monophyly constraints derived "
        "from Glottolog's classification.",
        getter=ConfigParser.getboolean)
    monophyletic = opt(
        None,
        "Backwards compat",
        getter=ConfigParser.getboolean)
    monophyly_start_depth = opt(
        0,
        "Integer; Starting depth in the Glottlog classification hierarchy for monophyly "
        "constraints.",
        getter=ConfigParser.getint)
    monophyly_end_depth = opt(
        None,
        "Integer; Number of levels of the Glottolog classification to include in monophyly "
        "constraints.",
        getter=ConfigParser.getint)
    monophyly_levels = opt(
        sys.maxsize,
        "Integer; Number of levels of the Glottolog classification to include in monophyly "
        "constraints.",
        getter=ConfigParser.getint)
    monophyly_newick = opt(
        None,
        "Either a Newick tree string or the name of a file containing a Newick tree string which "
        "represents the desired monophyly constraints if a classification other than Glottolog is "
        "required.",
        getter=functools.partial(get_tree, 'monophyly'))
    monophyly_direction = opt(
        "top_down",
        "Either the string 'top_down' or 'bottom_up', controlling whether 'monophyly_levels' "
        "counts from roots (families) or leaves (languages) of the Glottolog classification.",
        validator=attr.validators.in_(['top_down', 'bottom_up']),
        converter=lambda s: s.lower())
    tree_prior = opt(
        "yule",
        "Tree prior. Can be overridden by calibrations.",
        validator=attr.validators.in_(['yule', 'coalescent', 'birthdeath', 'uniform']),
        converter=lambda s: s.lower())

    def __attrs_post_init__(self):
        self.exclusions = set(self.exclusions)
        # ... and honor backwards-compat hacks:
        if self.monophyletic is not None:
            self.monophyly = self.monophyletic

    def sanitise_trees(self):
        """
        The list of languages in the analysis may also be derived from language specifications of
        models. Thus, proper pruning and sanitising of trees can only happen after models have been
        loaded. Once this is done, this method must be called.
        """
        if self.starting_tree:
            self.starting_tree = sanitise_tree(self.starting_tree, 'starting', self.languages)
        if self.monophyly_newick:
            self.monophyly_newick = sanitise_tree(
                self.monophyly_newick, 'monophyly', self.languages)
            self.monophyly = True


@attr.s
class Geography(Section):
    name = opt('geography')
    model = opt('geo')
    log_locations = opt(True, getter=ConfigParser.getboolean)
    sampling_points = opt(attr.Factory(list), getter=get_file_or_list)
    data = opt(attr.Factory(list), getter=get_list_of_files)
    priors = opt(attr.Factory(dict))
    clock = opt(None)


def maybe_get_float(cfg, section, option, default=None):
    try:
        return cfg.getfloat(section, option)
    except ValueError:
        return default


@attr.s
class Clock(Section):
    __allow_arbitrary_options__ = True

    name = opt(None, converter=lambda s: s[5:].strip())
    type = opt(
        'strict',
        validator=attr.validators.in_(set(cls.__type__ for cls in all_subclasses(BaseClock))))
    distribution = opt('lognormal', converter=lambda s: s.lower())
    mean = opt(None, getter=ConfigParser.getfloat)
    variance = opt(None, getter=ConfigParser.getfloat)
    rate = opt(None, getter=maybe_get_float)  # hope it's a prior clock if this fails.
    estimate_mean = opt(None, getter=ConfigParser.getboolean)
    estimate_rate = opt(None, getter=ConfigParser.getboolean)
    estimate_variance = opt(None, getter=ConfigParser.getboolean)
    correlated = opt(False, getter=ConfigParser.getboolean)

    def get_clock(self, global_config):
        clocks = [c for c in all_subclasses(BaseClock) if c.__type__ == self.type]
        if len(clocks) == 1:
            return clocks[0](self, global_config)
        # Multiple clocks with matching type. Let's discriminate by distribution:
        for clock in clocks:
            if clock.__distribution__ == self.distribution:
                return clock(self, global_config)
        raise ValueError('no matching clock')


def valid_path(instance, attribute, value):
    if not value.exists():
        raise ValueError('Path {0} does not exist'.format(value))


@attr.s
class Model(Section):
    __allow_arbitrary_options__ = True

    name = opt(
        None,
        converter=lambda s: s.split(None, 1)[1].strip().replace(' ', '_'))
    model = opt(
        None,
    )

    binarised = opt(None, getter=ConfigParser.getboolean)
    # "binarised" is the canonical name for this option and used everywhere
    # internally, but "binarized" is accepted in the config file.
    binarized = opt(None, getter=ConfigParser.getboolean)

    ascertained = opt(None, getter=ConfigParser.getboolean)
    pruned = opt(False, getter=ConfigParser.getboolean)
    use_robust_eigensystem = opt(False, getter=ConfigParser.getboolean)
    rate_variation = opt(False, getter=ConfigParser.getboolean)
    remove_constant_features = opt(True, getter=ConfigParser.getboolean)
    symmetric = opt(True, getter=ConfigParser.getboolean)
    share_params = opt(True, getter=ConfigParser.getboolean)

    minimum_data = opt(0.0, getter=ConfigParser.getfloat)

    features = opt(attr.Factory(lambda: ["*"]), getter=get_file_or_list)
    exclusions = opt(attr.Factory(list), getter=get_file_or_list)
    reconstruct = opt(attr.Factory(list), getter=get_file_or_list)
    reconstruct_at = opt(attr.Factory(list), getter=get_file_or_list)

    data = opt(
        None,
        converter=lambda s: pathlib.Path(s) if s else None,
        validator=attr.validators.optional(valid_path))

    def __attrs_post_init__(self):
        if self.binarized is not None and self.binarised is None:
            self.binarised = self.binarized

    def get_model(self, global_config):
        for cls in all_subclasses(BaseModel):
            if cls.__model_name__() == self.model:
                if cls.package_notice:
                    log.dependency(*cls.package_notice)
                return cls(self, global_config)
        try:
            module_path, class_name = self.model.rsplit(".", 1)
            cls = getattr(importlib.import_module(module_path), class_name)
            return cls(self, global_config)
        except:
            raise ValueError(
                "Unknown model type '%s' for model section '%s', and failed to import a "
                "third-party model." % (self.model, self.name))


@attr.s
class LanguageGroups(Section):
    __allow_arbitrary_options__ = True

    def __attrs_post_init__(self):
        self.options = collections.OrderedDict(
            [(k, [vv.strip() for vv in v.split(',')]) for k, v in self.options.items()])


@attr.s
class Calibration(Section):
    __allow_arbitrary_options__ = True


@attr.s
class GeoPriors(Section):
    __allow_arbitrary_options__ = True

    def iterpriors(self):
        for clades, kml in self.options.items():
            for clade in clades.split(','):
                yield clade.strip(), pathlib.Path(kml)
