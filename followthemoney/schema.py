from rdflib import URIRef  # type: ignore
from banal import ensure_list, ensure_dict, as_bool
from typing import Optional, Dict, Any, Set

from followthemoney.model import Model
from followthemoney.property import Property
from followthemoney.types import registry
from followthemoney.exc import InvalidData, InvalidModel
from followthemoney.util import gettext, NS


class Schema(object):
    """Defines the abstract data model.

    Schema items define the entities and links available in the model.
    """

    def __init__(self, model: Model, name: str, data: Dict[str, Any]):
        self.model: Model = model
        self.name: str = name
        self.data: Dict[str, Any] = data
        self._label: Optional[str] = data.get('label', name)
        self._plural: Optional[str] = data.get('plural', self.label)
        self._description: Optional[str] = data.get('description')
        self.uri = URIRef(data.get('rdf', NS[name]))

        # Do not show in listings:
        self.abstract = as_bool(data.get('abstract'), False)

        # Generated by the system, not user-managed
        self.generated = as_bool(data.get('generated'), False)

        # Try to perform fuzzy matching. Fuzzy similarity search does not
        # make sense for entities which have a lot of similar names, such
        # as land plots, assets etc.
        self.matchable = as_bool(data.get('matchable'), True)

        # Mark a set of properties as important, i.e. they should be shown
        # first, or in an abridged view of the entity.
        self.featured = ensure_list(data.get('featured'))

        # Mark a set of properties as required. This is applied only when
        # an entity is created by the user - bulk created entities will
        # slip through even if it is technically invalid.
        self.required = ensure_list(data.get('required'))

        # Mark a set of properties to be used for the entity's caption.
        # They will be checked in order and the first existant value will
        # be used.
        self.caption = ensure_list(data.get('caption'))

        # A transform of the entity into an edge for its representation in
        # the context of a property graph representation like Neo4J/Gephi.
        edge = data.get('edge', {})
        self.edge_source = edge.get('source')
        self.edge_target = edge.get('target')
        self.edge = self.edge_source and self.edge_target
        self.edge_caption = ensure_list(edge.get('caption'))
        self._edge_label = edge.get('label', self._label)
        self.edge_directed = edge.get('directed', True)

        self.extends: Set[str] = set()
        self.schemata = set([self])
        self.names = set([self.name])
        self.descendants: Set[str] = set()
        self.properties: Dict[str, Property] = {}
        for name, prop in data.get('properties', {}).items():
            self.properties[name] = Property(self, name, prop)

    def generate(self):
        for parent in ensure_list(self.data.get('extends')):
            parent = self.model.get(parent)
            parent.generate()

            for name, prop in parent.properties.items():
                if name not in self.properties:
                    self.properties[name] = prop

            self.extends.add(parent)
            for ancestor in parent.schemata:
                self.schemata.add(ancestor)
                self.names.add(ancestor.name)
                ancestor.descendants.add(self)

        for prop in list(self.properties.values()):
            prop.generate()

        for featured in self.featured:
            if self.get(featured) is None:
                raise InvalidModel("Missing featured property: %s" % featured)

        for caption in self.caption:
            if self.get(caption) is None:
                raise InvalidModel("Missing caption property: %s" % caption)

        for required in self.required:
            if self.get(required) is None:
                raise InvalidModel("Missing required property: %s" % required)

        if self.edge:
            if self.source_prop is None:
                msg = "Missing edge source: %s" % self.edge_source
                raise InvalidModel(msg)

            if self.target_prop is None:
                msg = "Missing edge target: %s" % self.edge_target
                raise InvalidModel(msg)

    def _add_reverse(self, data, other):
        name = data.get('name', None)
        if name is None:
            raise InvalidModel("Unnamed reverse: %s" % other)

        prop = self.get(name)
        if prop is None:
            data.update({
                'type': registry.entity.name,
                'reverse': {'name': other.name},
                'range': other.schema.name,
                'stub': True
            })
            data['hidden'] = data.get('hidden', other.hidden)
            prop = Property(self, name, data)
            prop.generate()
            self.properties[name] = prop
        return prop

    @property
    def label(self):
        return gettext(self._label)

    @property
    def plural(self):
        return gettext(self._plural)

    @property
    def description(self):
        return gettext(self._description)

    @property
    def edge_label(self):
        return gettext(self._edge_label)

    @property
    def source_prop(self):
        return self.get(self.edge_source)

    @property
    def target_prop(self):
        return self.get(self.edge_target)

    @property
    def sorted_properties(self):
        return sorted(self.properties.values(),
                      key=lambda p: (p.name not in self.caption,
                                     p.name not in self.featured,
                                     p.label))

    @property
    def matchable_schemata(self):
        """The set of comparable types."""
        if not self.matchable:
            return
        # This is used by the cross-referencer to determine what
        # other schemata should be considered for matches. For
        # example, a Company may be compared to a Legal Entity,
        # but it makes no sense to compare it to an Aircraft.
        matchable = set(self.schemata)
        matchable.update(self.descendants)
        for schema in matchable:
            if schema.matchable:
                yield schema

    def is_a(self, parent: 'Schema') -> bool:
        return parent in self.schemata

    def get(self, name):
        return self.properties.get(name)

    def validate(self, data):
        """Validate a dataset against the given schema.
        This will also drop keys which are not present as properties.
        """
        errors = {}
        properties = ensure_dict(data.get('properties'))
        for name, prop in self.properties.items():
            values = ensure_list(properties.get(name))
            error = prop.validate(values)
            if error is None and not len(values):
                if prop.name in self.required:
                    error = gettext('Required')
            if error is not None:
                errors[name] = error
        if len(errors):
            msg = gettext("Entity validation failed")
            raise InvalidData(msg, errors={'properties': errors})

    def to_dict(self):
        data = {
            'label': self.label,
            'plural': self.plural,
            'schemata': list(sorted(self.names)),
            'extends': list(sorted([e.name for e in self.extends])),
            'properties': {}
        }
        if self.edge_source and self.edge_target:
            data['edge'] = {
                'source': self.edge_source,
                'target': self.edge_target,
                'caption': self.edge_caption,
                'label': self.edge_label,
                'directed': self.edge_directed
            }
        if len(self.featured):
            data['featured'] = self.featured
        if len(self.required):
            data['required'] = self.required
        if len(self.caption):
            data['caption'] = self.caption
        if self.description:
            data['description'] = self.description
        if self.abstract:
            data['abstract'] = True
        if self.generated:
            data['generated'] = True
        if self.matchable:
            data['matchable'] = True
        for name, prop in self.properties.items():
            if prop.schema == self:
                data['properties'][name] = prop.to_dict()
        return data

    def __eq__(self, other):
        return hash(other) == hash(self)

    def __hash__(self):
        return hash(self.name)

    def __repr__(self):
        return '<Schema(%r)>' % self.name
