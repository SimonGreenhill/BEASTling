import datetime
import itertools
from math import log
import sys
import xml.etree.ElementTree as ET

from six import BytesIO, PY3

from beastling import __version__
import beastling.beast_maps as beast_maps

def indent(elem, level=0):
    i = "\n" + level*"  "
    if len(elem):
        if not elem.text or not elem.text.strip():
            elem.text = i + "  "
        if not elem.tail or not elem.tail.strip():
            elem.tail = i
        for elem in elem:
            indent(elem, level+1)
        if not elem.tail or not elem.tail.strip():
            elem.tail = i
    else:
        if level and (not elem.tail or not elem.tail.strip()):
            elem.tail = i

class BeastXml(object):

    def __init__(self, config):
        self.config = config
        if not self.config.processed:
            self.config.process()
        self._covarion_userdatatype_created = False
        # Tell everybody about ourselves
        for model in self.config.all_models:
            model.beastxml = self
        for clock in self.config.clocks:
            clock.beastxml = self
        self._taxon_sets = {}
        self.build_xml()

    def build_xml(self):
        """
        Creates a complete BEAST XML configuration file as an ElementTree,
        descending from the self.beast element.
        """
        attribs = {}
        attribs["beautitemplate"] = "Standard"
        attribs["beautistatus"] = ""
        attribs["namespace"] = "beast.core:beast.evolution.alignment:beast.evolution.tree.coalescent:beast.core.util:beast.evolution.nuc:beast.evolution.operators:beast.evolution.sitemodel:beast.evolution.substitutionmodel:beast.evolution.likelihood"
        attribs["version"] ="2.0"
        self.beast = ET.Element("beast", attrib=attribs)
        self.add_beastling_comment()
        self.embed_data()
        self.add_maps()
        for model in self.config.models:
            model.add_master_data(self.beast)
            model.add_misc(self.beast)
        for clock in self.config.clocks:
            clock.add_branchrate_model(self.beast)
        self.add_run()

    def add_beastling_comment(self):
        """
        Add a comment at the root level of the XML document indicating the
        BEASTling version used to create the file, the time and date of
        generation and the original configuration file text.
        """
        comment_lines = []
        comment_lines.append("Generated by BEASTling %s on %s." % (__version__,datetime.datetime.now().strftime("%A, %d %b %Y %I:%M %p")))
        if self.config.configfile:
            comment_lines.append("Original config file:")
            comment_lines.append(self.config.configfile.write_string())
        else:
            comment_lines.append("Configuration built programmatically.")
            comment_lines.append("No config file to include.")
        self.beast.append(ET.Comment("\n".join(comment_lines)))

    def embed_data(self):
        """
        Embed a copy of each data file in a comment at the top of the XML
        document.
        """
        if not self.config.embed_data:
            return
        for filename in self.config.files_to_embed:
            self.beast.append(self.format_data_file(filename))
        for model in self.config.models:
            self.beast.append(self.format_data_file(model.data_filename))

    def format_data_file(self, filename):
        """
        Return an ElementTree node corresponding to a comment containing
        the text of the specified data file.
        """
        header = "BEASTling embedded data file: %s" % filename
        fp = open(filename, "r")
        data_block = "\n".join([header, fp.read()])
        fp.close()
        return ET.Comment(data_block)

    def add_maps(self):
        """
        Add <map> elements aliasing common BEAST classes.
        """
        for a, b in beast_maps.maps:
            mapp = ET.SubElement(self.beast, "map", attrib={"name":a})
            mapp.text = b

    def add_run(self):
        """
        Add the <run> element and all its descendants, which is most of the
        analysis.
        """
        attribs = {}
        attribs["id"] = "mcmc"
        attribs["spec"] = "MCMC"
        attribs["chainLength"] = str(self.config.chainlength)
        if self.config.sample_from_prior:
            attribs["sampleFromPrior"] = "true"
        self.run = ET.SubElement(self.beast, "run", attrib=attribs)
        self.add_state()
        self.add_init()
        self.add_distributions()
        self.add_operators()
        self.add_loggers()

    def add_state(self):
        """
        Add the <state> element and all its descendants.
        """
        self.state = ET.SubElement(self.run, "state", {"id":"state","storeEvery":"5000"})
        self.add_tree_state()
        for clock in self.config.clocks:
            clock.add_state(self.state)
        for model in self.config.all_models:
            model.add_state(self.state)

    def add_tree_state(self):
        """
        Add tree-related <state> sub-elements.
        """
        tree = ET.SubElement(self.state, "tree", {"id":"Tree.t:beastlingTree", "name":"stateNode"})
        self.add_taxon_set(tree, "taxa", self.config.languages, define_taxa=True)
        param = ET.SubElement(self.state, "parameter", {"id":"birthRate.t:beastlingTree","name":"stateNode"})
        param.text="1.0"

    def add_init(self):
        """
        Add the <init> element and all its descendants.
        """
        # If a starting tree is specified, use it...
        if self.config.starting_tree:
            init = ET.SubElement(self.run, "init", {"estimate":"false", "id":"startingTree", "initial":"@Tree.t:beastlingTree", "spec":"beast.util.TreeParser","IsLabelledNewick":"true", "newick":self.config.starting_tree})
        # ...if not, use the simplest random tree initialiser possible
        else:
            # If we have non-trivial monophyly constraints, use ConstrainedRandomTree
            if self.config.monophyly and len(self.config.languages) > 2:
                self.add_constrainedrandomtree_init()
            # If we have hard-bound calibrations, use SimpleRandomTree
            elif any([c.dist == "uniform" for c in self.config.calibrations.values()]):
                self.add_simplerandomtree_init()
            # Otherwise, just use RandomTree
            else:
                self.add_randomtree_init()

    def add_randomtree_init(self):
        init = ET.SubElement(self.run, "init", {"estimate":"false", "id":"startingTree", "initial":"@Tree.t:beastlingTree", "taxonset":"@taxa", "spec":"beast.evolution.tree.RandomTree"})
        popmod = ET.SubElement(init, "populationModel", {"spec":"ConstantPopulation"})
        ET.SubElement(popmod, "popSize", {"spec":"parameter.RealParameter","value":"1"})

    def add_simplerandomtree_init(self):
        ET.SubElement(self.run, "init", {"estimate":"false", "id":"startingTree", "initial":"@Tree.t:beastlingTree", "taxonset":"@taxa", "spec":"beast.evolution.tree.SimpleRandomTree"})

    def add_constrainedrandomtree_init(self):
        init = ET.SubElement(self.run, "init", {"estimate":"false", "id":"startingTree", "initial":"@Tree.t:beastlingTree", "taxonset":"@taxa", "spec":"beast.evolution.tree.ConstrainedRandomTree", "constraints":"@constraints"})
        popmod = ET.SubElement(init, "populationModel", {"spec":"ConstantPopulation"})
        ET.SubElement(popmod, "popSize", {"spec":"parameter.RealParameter","value":"1"})

    def add_distributions(self):
        """
        Add all probability distributions under the <run> element.
        """
        self.master_distribution = ET.SubElement(self.run,"distribution",{"id":"posterior","spec":"util.CompoundDistribution"})
        self.add_prior()
        self.add_likelihood()

    def add_prior(self):
        """
        Add all prior distribution elements.
        """
        self.prior = ET.SubElement(self.master_distribution,"distribution",{"id":"prior","spec":"util.CompoundDistribution"})
        self.add_monophyly_constraints()
        self.add_calibrations()
        self.add_tree_prior()
        for clock in self.config.clocks:
            clock.add_prior(self.prior)
        for model in self.config.all_models:
            model.add_prior(self.prior)

    def add_monophyly_constraints(self):
        """
        Add monophyly constraints to prior distribution.
        """
        if self.config.monophyly:
            attribs = {}
            attribs["id"] = "constraints"
            attribs["spec"] = "beast.math.distributions.MultiMonophyleticConstraint"
            attribs["tree"] = "@Tree.t:beastlingTree"
            attribs["newick"] = self.config.monophyly_newick
            ET.SubElement(self.prior, "distribution", attribs)

    def add_calibrations(self):
        """
        Add timing calibrations to prior distribution.
        """
        p1_names = {"Normal":"mean", "LogNormal":"M","Uniform":"lower"}
        p2_names = {"Normal":"sigma", "LogNormal":"S","Uniform":"upper"}
        for clade, cal in sorted(self.config.calibrations.items()):

            # Create MRCAPrior node
            attribs = {}
            attribs["id"] = clade + "MRCA"
            attribs["monophyletic"] = "true"
            attribs["spec"] = "beast.math.distributions.MRCAPrior"
            attribs["tree"] = "@Tree.t:beastlingTree"
            if cal.originate:
                attribs["useOriginate"] = "true"
            cal_prior = ET.SubElement(self.prior, "distribution", attribs)

            # Create "taxonset" param for MRCAPrior
            taxonsetname = clade[:-len("_originate")] if clade.endswith("_originate") else clade
            self.add_taxon_set(cal_prior, taxonsetname, cal.langs)

            # Create "distr" param for MRCAPrior
            dist_type = {"normal":"Normal","lognormal":"LogNormal","uniform":"Uniform"}[cal.dist]
            attribs = {"id":"CalibrationDistribution.%s" % clade, "name":"distr", "offset":"0.0"}
            if dist_type == "Uniform":
                attribs["lower"] = str(cal.param1)
                attribs["upper"] = str(cal.param2)
            dist = ET.SubElement(cal_prior, dist_type, attribs)
            if dist_type != "Uniform":
                ET.SubElement(dist, "parameter", {"id":"CalibrationDistribution.%s.param1" % clade, "name":p1_names[dist_type], "estimate":"false"}).text = str(cal.param1)
                ET.SubElement(dist, "parameter", {"id":"CalibrationDistribution.%s.param2" % clade, "name":p2_names[dist_type], "estimate":"false"}).text = str(cal.param2)

    def add_taxon_set(self, parent, label, langs, define_taxa=False):
        """
        Add a TaxonSet element with the specified set of languages.

        If a TaxonSet previously defined by this method contains exactly the
        same set of taxa, a reference to that TaxonSet will be added instead.
        By default, each TaxonSet will contain references to the taxa,
        assuming that they have been defined previously (most probably in the
        definition of the tree).  If this is not the case, passing
        define_taxa=True will define, rather than refer to, the taxa.
        """
        # Refer to any previous TaxonSet with the same languages
        for idref, taxa in self._taxon_sets.items():
            if set(langs) == taxa:
                ET.SubElement(parent, "taxonset", {"idref" : idref, "spec":"TaxonSet"})
                return
        # Otherwise, create and register a new TaxonSet
        taxonset = ET.SubElement(parent, "taxonset", {"id" : label, "spec":"TaxonSet"})
        plate = ET.SubElement(taxonset, "plate", {
            "var":"language",
            "range":",".join(langs)})
        ET.SubElement(plate, "taxon", {"id" if define_taxa else "idref" :"$(language)"})
        self._taxon_sets[label] = set(langs)

    def add_tree_prior(self):
        """
        Add Yule birth-process tree prior.
        """
        # Tree prior
        attribs = {}
        attribs["birthDiffRate"] = "@birthRate.t:beastlingTree"
        attribs["id"] = "YuleModel.t:beastlingTree"
        attribs["spec"] = "beast.evolution.speciation.YuleModel"
        attribs["tree"] = "@Tree.t:beastlingTree"
        ET.SubElement(self.prior, "distribution", attribs)

        # Birth rate
        attribs = {}
        attribs["id"] = "YuleBirthRatePrior.t:beastlingTree"
        attribs["name"] = "distribution"
        attribs["x"] = "@birthRate.t:beastlingTree"
        sub_prior = ET.SubElement(self.prior, "prior", attribs)
        uniform = ET.SubElement(sub_prior, "Uniform", {"id":"Uniform.0","name":"distr","upper":"Infinity"})

    def add_likelihood(self):
        """
        Add all likelihood distribution elements.
        """
        self.likelihood = ET.SubElement(self.master_distribution,"distribution",{"id":"likelihood","spec":"util.CompoundDistribution"})
        for model in self.config.all_models:
            model.add_likelihood(self.likelihood)

    def add_operators(self):
        """
        Add all <operator> elements.
        """
        self.add_tree_operators()
        for clock in self.config.clocks:
            clock.add_operators(self.run)
        for model in self.config.all_models:
            model.add_operators(self.run)
        # Add one DeltaExchangeOperator for feature rates per clock
        for clock in self.config.clocks:
            clock_models = [m for m in self.config.models if m.rate_variation and m.clock == clock]
            if not clock_models:
                continue
            # Add one big DeltaExchangeOperator which operates on all
            # feature clock rates from all models
            delta = ET.SubElement(self.run, "operator", {"id":"featureClockRateDeltaExchanger:%s" % clock.name, "spec":"DeltaExchangeOperator", "weight":"3.0"})
            for model in clock_models:
                plate = ET.SubElement(delta, "plate", {
                    "var":"feature",
                    "range":",".join(model.features)})
                ET.SubElement(plate, "parameter", {"idref":"featureClockRate:%s:$(feature)" % model.name})
            # Add weight vector if there has been any binarisation
            if any([w != 1 for w in itertools.chain(*[m.weights.values() for m in clock_models])]):
                weightvector = ET.SubElement(delta, "weightvector", {
                    "id":"featureClockRateWeightParameter:%s" % clock.name,
                    "spec":"parameter.IntegerParameter",
                    "dimension":str(sum([len(m.weights) for m in clock_models])),
                    "estimate":"false"
                })
                weightvector.text = " ".join(itertools.chain(*[[str(m.weights[f]) for f in m.features] for m in clock_models]))


    def add_tree_operators(self):
        """
        Add all <operator>s which act on the tree topology and branch lengths.
        """
        # Tree operators
        # Operators which affect the tree must respect the sample_topology and
        # sample_branch_length options.
        if self.config.sample_topology:
            ## Tree topology operators
            ET.SubElement(self.run, "operator", {"id":"SubtreeSlide.t:beastlingTree","spec":"SubtreeSlide","tree":"@Tree.t:beastlingTree","markclades":"true", "weight":"15.0"})
            ET.SubElement(self.run, "operator", {"id":"narrow.t:beastlingTree","spec":"Exchange","tree":"@Tree.t:beastlingTree","markclades":"true", "weight":"15.0"})
            ET.SubElement(self.run, "operator", {"id":"wide.t:beastlingTree","isNarrow":"false","spec":"Exchange","tree":"@Tree.t:beastlingTree","markclades":"true", "weight":"3.0"})
            ET.SubElement(self.run, "operator", {"id":"WilsonBalding.t:beastlingTree","spec":"WilsonBalding","tree":"@Tree.t:beastlingTree","markclades":"true","weight":"3.0"})
        if self.config.sample_branch_lengths:
            ## Branch length operators
            ET.SubElement(self.run, "operator", {"id":"UniformOperator.t:beastlingTree","spec":"Uniform","tree":"@Tree.t:beastlingTree","weight":"30.0"})
            ET.SubElement(self.run, "operator", {"id":"treeScaler.t:beastlingTree","scaleFactor":"0.5","spec":"ScaleOperator","tree":"@Tree.t:beastlingTree","weight":"3.0"})
            ET.SubElement(self.run, "operator", {"id":"treeRootScaler.t:beastlingTree","scaleFactor":"0.5","spec":"ScaleOperator","tree":"@Tree.t:beastlingTree","rootOnly":"true","weight":"3.0"})
            ## Up/down operator which scales tree height
            updown = ET.SubElement(self.run, "operator", {"id":"UpDown","spec":"UpDownOperator","scaleFactor":"0.5", "weight":"3.0"})
            ET.SubElement(updown, "tree", {"idref":"Tree.t:beastlingTree", "name":"up"})
            ET.SubElement(updown, "parameter", {"idref":"birthRate.t:beastlingTree", "name":"down"})
            ### Include clock rates in up/down only if calibrations are given
            if self.config.calibrations:
                for clock in self.config.clocks:
                    ET.SubElement(updown, "parameter", {"idref":clock.mean_rate_id, "name":"down"})

        # Birth rate scaler
        # Birth rate is *always* scaled.
        ET.SubElement(self.run, "operator", {"id":"YuleBirthRateScaler.t:beastlingTree","spec":"ScaleOperator","parameter":"@birthRate.t:beastlingTree", "scaleFactor":"0.5", "weight":"3.0"})

    def add_loggers(self):
        """
        Add all <logger> elements.
        """
        self.add_screen_logger()
        self.add_tracer_logger()
        self.add_tree_loggers()

    def add_screen_logger(self):
        """
        Add the screen logger, if configured to do so.
        """
        if not self.config.screenlog:
            return
        screen_logger = ET.SubElement(self.run, "logger", attrib={"id":"screenlog", "logEvery":str(self.config.log_every)})
        log = ET.SubElement(screen_logger, "log", attrib={"arg":"@posterior", "id":"ESS.0", "spec":"util.ESS"})
        log = ET.SubElement(screen_logger, "log", attrib={"idref":"prior"})
        log = ET.SubElement(screen_logger, "log", attrib={"idref":"likelihood"})
        log = ET.SubElement(screen_logger, "log", attrib={"idref":"posterior"})

    def add_tracer_logger(self):
        """
        Add file logger, if configured to do so.
        """
        if not(self.config.log_probabilities or self.config.log_params or self.config.log_all):
            return
        tracer_logger = ET.SubElement(self.run,"logger",{"id":"tracelog","fileName":self.config.basename+".log","logEvery":str(self.config.log_every),"sort":"smart"})
        # Log prior, likelihood and posterior
        if self.config.log_probabilities or self.config.log_all:
            ET.SubElement(tracer_logger,"log",{"idref":"prior"})
            ET.SubElement(tracer_logger,"log",{"idref":"likelihood"})
            ET.SubElement(tracer_logger,"log",{"idref":"posterior"})
        # Log Yule birth rate
        if self.config.log_params or self.config.log_all:
            ET.SubElement(tracer_logger,"log",{"idref":"birthRate.t:beastlingTree"})
            for clock in self.config.clocks:
                clock.add_param_logs(tracer_logger)
            for model in self.config.all_models:
                    model.add_param_logs(tracer_logger)

        # Log tree height
        if not self.config.tree_logging_pointless:
            ET.SubElement(tracer_logger,"log",{
                "id":"treeHeight",
                "spec":"beast.evolution.tree.TreeHeightLogger",
                "tree":"@Tree.t:beastlingTree"})

        # Log calibration clade heights
        for clade in sorted(self.config.calibrations.keys()):
            ET.SubElement(tracer_logger,"log",{"idref":"%sMRCA" % clade})

        # Fine-grained logging
        if self.config.log_fine_probs:
            ET.SubElement(tracer_logger,"log",{"idref":"YuleModel.t:beastlingTree"})
            ET.SubElement(tracer_logger,"log",{"idref":"YuleBirthRatePrior.t:beastlingTree"})

    def add_tree_loggers(self):
        """
        Add tree logger, if configured to do so.
        """
        if not ((self.config.log_trees or self.config.log_all) and not
            self.config.tree_logging_pointless):
            return

        pure_tree_done = False
        non_strict_clocks = set([m.clock for m in self.config.models if not m.clock.is_strict])
        if not non_strict_clocks:
            # All clocks are strict, so we just do one pure log file
            self.add_tree_logger()
            pure_tree_done = True
        else:
            # There are non-strict clocks, so we do one log file each with branch rates
            for clock in non_strict_clocks:
                if len(non_strict_clocks) == 1:
                    self.add_tree_logger("", clock.branchrate_model_id)
                else:
                    self.add_tree_logger("_%s_rates" % clock.name, clock.branchrate_model_id)

        # If asked, do a topology-only tree log (i.e. no branch rates)
        if self.config.log_pure_tree and not pure_tree_done:
            self.add_tree_logger("_pure")


        # Created a dedicated geographic tree log if asked to log locations,
        # or if the geo model's clock is non-strict
        if not self.config.geo_config:
            return
        if self.config.geo_config["log_locations"] or not self.config.geo_model.clock.is_strict:
            self.add_tree_logger("_geography", self.config.geo_model.clock.branchrate_model_id, True)

    def add_tree_logger(self, suffix="", branchrate_model_id=None, locations=False):
        tree_logger = ET.SubElement(self.run, "logger", {"mode":"tree", "fileName":self.config.basename + suffix + ".nex", "logEvery":str(self.config.log_every),"id":"treeLogger" + suffix})
        log = ET.SubElement(tree_logger, "log", attrib={"id":"TreeLoggerWithMetaData"+suffix,"spec":"beast.evolution.tree.TreeWithMetaDataLogger","tree":"@Tree.t:beastlingTree"})
        if branchrate_model_id:
            ET.SubElement(log, "branchratemodel", {"idref":branchrate_model_id})
        if locations:
            ET.SubElement(log, "metadata", {
                "id":"location",
                "spec":"sphericalGeo.TraitFunction",
                "likelihood":"@sphericalGeographyLikelihood"}).text = "0.0"

    def tostring(self):
        """
        Return a string representation of the entire XML document.
        """
        out = BytesIO()
        self.write(out)
        out.seek(0)
        return out.read()

    def write(self, stream):
        indent(self.beast)
        tree = ET.ElementTree(self.beast)
        tree.write(stream, encoding='UTF-8', xml_declaration=True)

    def write_file(self, filename=None):
        """
        Write the XML document to a file.
        """
        if filename in ("stdout", "-"):
            # See https://docs.python.org/3/library/sys.html#sys.stdout
            self.write(getattr(sys.stdout, 'buffer', sys.stdout) if PY3 else sys.stdout)
        else:
            with open(filename or self.config.basename + ".xml", "wb") as stream:
                self.write(stream)
