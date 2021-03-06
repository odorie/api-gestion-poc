from datetime import datetime

import decorator
import peewee

from ban import db
from ban.auth.models import Client, Session
from ban.utils import make_diff, utcnow

from . import context, resource
from .exceptions import (IsDeletedError, MultipleRedirectsError, RedirectError)


@decorator.decorator
def contributor_type_required(func, self, *args, **kwargs):
    session = context.get('session')
    if not session:
        raise ValueError('Must be logged in.')
    if not session.client:
        raise ValueError('Token must be linked to a client.')
    if not session.contributor_type:
        raise ValueError('Session must have a valid contributor_type.')
    if session.contributor_type == Client.TYPE_VIEWER:
        raise ValueError('Contributor type viewer cannot flag/unflag resource.')

    # Even if session is declared as kwarg, "decorator" helper injects it
    # as arg. Bad.
    args = list(args)
    args[0] = session
    func(self, *args, **kwargs)


class ForcedVersionError(Exception):
    pass


class BaseVersioned(peewee.BaseModel):

    registry = {}

    def __new__(mcs, name, bases, attrs, **kwargs):
        cls = super().__new__(mcs, name, bases, attrs, **kwargs)
        BaseVersioned.registry[name.lower()] = cls
        return cls


class Versioned(db.Model, metaclass=BaseVersioned):

    ForcedVersionError = ForcedVersionError

    version = db.IntegerField(default=1)
    created_at = db.DateTimeField()
    created_by = db.CachedForeignKeyField(Session)
    modified_at = db.DateTimeField()
    modified_by = db.CachedForeignKeyField(Session)

    class Meta:
        validate_backrefs = False
        unique_together = ('pk', 'version')

    def prepared(self):
        self.lock_version()
        super().prepared()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.prepared()

    def store_version(self):
        new = Version.create(
            model_name=self.resource,
            model_pk=self.pk,
            sequential=self.version,
            data=self.as_version,
            period=[self.modified_at, None]
        )
        old = None
        if self.version > 1:
            old = self.load_version(self.version - 1)
            old.close_period(new.period.lower)
        if Diff.ACTIVE:
            Diff.create(old=old, new=new, created_at=self.modified_at,
                        insee=self.municipality.insee)

    @property
    def versions(self):
        return Version.select().where(
            Version.model_name == self.resource,
            Version.model_pk == self.pk).order_by(Version.sequential)

    def load_version(self, ref=None):
        qs = self.versions
        if ref is None:
            ref = self.version
        if isinstance(ref, datetime):
            qs = qs.where(Version.period.contains(ref))
        else:
            qs = qs.where(Version.sequential == ref)
        return qs.first()

    @property
    def locked_version(self):
        return getattr(self, '_locked_version', None)

    @locked_version.setter
    def locked_version(self, value):
        # Should be set only once, and never updated.
        assert not hasattr(self, '_locked_version'), 'locked_version is read only'  # noqa
        self._locked_version = value

    def lock_version(self):
        if not self.pk:
            self.version = 1
        self._locked_version = self.version if self.pk else 0

    def increment_version(self):
        self.version = self.version + 1

    def check_version(self):
        if self.version != self.locked_version + 1:
            raise ForcedVersionError('wrong version number: {}'.format(self.version))  # noqa

    def update_meta(self):
        session = context.get('session')
        if session:
            if not self.created_by:
                self.created_by = session
            self.modified_by = session
        now = utcnow()
        if not self.created_at:
            self.created_at = now
        self.modified_at = now

    def save(self, *args, **kwargs):
        with self._meta.database.atomic():
            self.check_version()
            self.update_meta()
            try:
                self.source_kind = self.created_by.contributor_type
            except Exception:
                pass
            super().save(*args, **kwargs)
            self.store_version()
            self.lock_version()

    def delete_instance(self, *args, **kwargs):
        with self._meta.database.atomic():
            Redirect.clear(self)
            return super().delete_instance(*args, **kwargs)


class Version(db.Model):

    __openapi__ = """
        properties:
            data:
                type: object
                description: serialized resource
            flag:
                type: array
                items:
                    $ref: '#/definitions/Flag'
        """

    model_name = db.CharField(max_length=64)
    model_pk = db.IntegerField()
    sequential = db.IntegerField()
    data = db.BinaryJSONField()
    period = db.DateRangeField()

    class Meta:
        indexes = (
            (('model_name', 'model_pk', 'sequential'), True),
        )

    def __repr__(self):
        return '<Version {} of {}({})>'.format(self.sequential,
                                               self.model_name, self.model_pk)

    def serialize(self, *args):
        return {
            'data': self.data,
            'flags': list(self.flags.serialize())
        }

    @property
    def model(self):
        return BaseVersioned.registry[self.model_name]

    def load(self):
        validator = self.model.validator(**self.data)
        return self.model(**validator.data)

    @property
    def diff(self):
        return Diff.first(Diff.new == self.pk)

    @contributor_type_required
    def flag(self, session=None):
        """Flag current version with current client."""
        if not Flag.where(Flag.version == self,
                          Flag.client == session.client).exists():
            Flag.create(version=self, session=session, client=session.client)

    @contributor_type_required
    def unflag(self, session=None):
        """Delete current version's flags made by current session client."""
        Flag.delete().where(Flag.version == self,
                            Flag.client == session.client).execute()

    def close_period(self, bound):
        # DateTimeRange is immutable, so create new one.
        self.period = [self.period.lower, bound]
        self.save()

    @classmethod
    def raw_select(cls, *selection):
        return super().select(*selection)

    @classmethod
    def coerce(cls, id, identifier=None, level1=0):
        if isinstance(id, db.Model):
            instance = id
        else:
            if not identifier:
                identifier = 'sequential'  # BAN id by default.
                if isinstance(id, str):
                    *extra, id = id.split(':')
                    if extra:
                        identifier = extra[0]
                elif isinstance(id, int):
                    identifier = 'pk'
            try:
                instance = cls.raw_select().where(
                    getattr(cls, identifier) == id).get()
            except cls.DoesNotExist:
                # Is it an old identifier?
                redirects = Redirect.follow(cls.__name__, identifier, id)
                if redirects:
                    if len(redirects) > 1:
                        raise MultipleRedirectsError(identifier, id, redirects)
                    raise RedirectError(identifier, id, redirects[0])
                raise
        return instance


class Diff(db.Model):

    __openapi__ = """
        properties:
            increment:
                type: integer
                description: incremental id of the diff
            resource:
                type: string
                description: name of the resource the diff is applied to
            resource_id:
                type: string
                description: id of the resource the diff is applied to
            created_at:
                type: string
                format: date-time
                description: the date and time the diff has been created at
            insee:
                type: string
                description: INSEE code of the Municipality the resource
                             is attached
            old:
                type: object
                description: the resource before the change
            new:
                type: object
                description: the resource after the change
            diff:
                type: object
                description: detail of changed properties
            """

    # Allow to skip diff at very first data import.
    ACTIVE = True

    # old is empty at creation.
    old = db.ForeignKeyField(Version, null=True)
    # new is empty after delete.
    new = db.ForeignKeyField(Version, null=True)
    insee = db.CharField(length=5)
    diff = db.BinaryJSONField()
    created_at = db.DateTimeField()

    class Meta:
        validate_backrefs = False
        order_by = ('pk', )

    def save(self, *args, **kwargs):
        if not self.diff:
            old = self.old.data if self.old else {}
            new = self.new.data if self.new else {}
            self.diff = make_diff(old, new)
        super().save(*args, **kwargs)
        Redirect.from_diff(self)

    def serialize(self, *args):
        version = self.new or self.old
        return {
            'increment': self.pk,
            'insee': self.insee,
            'old': self.old.data if self.old else None,
            'new': self.new.data if self.new else None,
            'diff': self.diff,
            'resource': version.model_name.lower(),
            'resource_id': version.data['id'],
            'created_at': self.created_at
        }


class Redirect(db.Model):

    __openapi__ = """
        properties:
            identifier:
                type: string
                description:
                    key/value pair for identifier.
                        . key = identifier name. e.g., 'id'.
                        . value = identifier value.
                        . key and value are separated by a ':'
        """

    model_name = db.CharField(max_length=64)
    model_id = db.CharField(max_length=255)
    identifier = db.CharField(max_length=64)
    value = db.CharField(max_length=255)

    class Meta:
        primary_key = peewee.CompositeKey('model_name', 'identifier', 'value',
                                          'model_id')

    @classmethod
    def add(cls, instance, identifier, value):
        if isinstance(instance, tuple):
            # Optim so we don't need to request db when creating a redirect
            # from a diff.
            model_name, model_id = instance
        else:
            model_name = instance.resource
            model_id = instance.id
            if identifier not in instance.__class__.identifiers + ['id', 'pk']:
                raise ValueError('Invalid identifier: {}'.format(identifier))
            if getattr(instance, identifier) == value:
                raise ValueError('Redirect cannot point to itself')
        cls.get_or_create(model_name=model_name,
                          identifier=identifier,
                          value=str(value), model_id=model_id)
        cls.propagate(model_name, identifier, value, model_id)

    @classmethod
    def remove(cls, instance, identifier, value):
        cls.delete().where(cls.model_name == instance.resource,
                           cls.identifier == identifier,
                           cls.value == str(value),
                           cls.model_id == instance.id).execute()

    @classmethod
    def clear(cls, instance):
        cls.delete().where(cls.model_name == instance.resource,
                           cls.model_id == instance.id).execute()

    @classmethod
    def from_diff(cls, diff):
        if not diff.new or not diff.old:
            # Only update makes sense for us, not creation nor deletion.
            return
        model = diff.new.model
        identifiers = [i for i in model.identifiers if i in diff.diff]
        for identifier in identifiers:
            old = diff.diff[identifier]['old']
            new = diff.diff[identifier]['new']
            if not old or not new:
                continue
            cls.add((model.__name__.lower(), diff.new.data['id']),
                    identifier, old)

    @classmethod
    def follow(cls, model_name, identifier, value):
        rows = cls.select(cls.model_id).where(
            cls.model_name == model_name.lower(),
            cls.identifier == identifier,
            cls.value == str(value))
        return [row.model_id for row in rows]

    @classmethod
    def propagate(cls, model_name, identifier, value, model_id):
        """An identifier was a target and it becomes itself a redirect."""
        model = BaseVersioned.registry.get(model_name)
        if model:
            old = model.first(getattr(model, identifier) == value)
            if old:
                cls.update(model_id=model_id).where(
                    cls.model_id == old.id,
                    cls.model_name == model_name).execute()

    def serialize(self, *args):
        return '{}:{}'.format(self.identifier, self.value)


class Flag(db.Model):

    __openapi__ = """
        properties:
            at:
                type: string
                format: date-time
                description: when the flag has been created
            by:
                type: string
                description: identifier of the client who flagged the version
        """

    version = db.ForeignKeyField(Version, related_name='flags')
    client = db.ForeignKeyField(Client)
    session = db.ForeignKeyField(Session)
    created_at = db.DateTimeField()

    def save(self, *args, **kwargs):
        if not self.created_at:
            self.created_at = utcnow()
        super().save(*args, **kwargs)

    def serialize(self, *args):
        return {
            'at': self.created_at,
            'by': self.session.contributor_type
        }


class Anomaly(resource.ResourceModel):

    __openapi__ = """
            properties:
                identifier:
                    type: string
                    description:
                        key/value pair for identifier.
                            . key = identifier name. e.g., 'id'.
                            . value = identifier value.
                            . key and value are separated by a ':'
            """

    resource_fields = ['versions', 'kind', 'insee', 'created_at']
    readonly_fields = (resource.ResourceModel.readonly_fields + ['created_at'])
    versions = db.ManyToManyField(Version, related_name='_anomalies')
    kind = db.CharField()
    insee = db.CharField(length=5)
    created_at = db.DateTimeField()
    legitimate = db.BooleanField(default=False)

    def save(self, *args, **kwargs):
        if not self.created_at:
            self.created_at = utcnow()
        return super().save(*args, **kwargs)

    def mark_deleted(self):
        self.delete_instance()