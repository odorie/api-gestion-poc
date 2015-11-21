from pathlib import Path

from ban.commands.importer import municipalities
from ban.commands.auth import createuser
from ban.commands.db import truncate
from ban.core import models
from ban.core.versioning import Diff
from ban.auth import models as amodels
from ban.tests import factories


def test_import_municipalities(staff, monkeypatch):
    path = Path(__file__).parent / 'data/municipalities.csv'
    municipalities(path)
    assert len(models.Municipality.select()) == 4
    assert not len(Diff.select())


def test_import_municipalities_can_be_filtered_by_departement(staff):
    path = Path(__file__).parent / 'data/municipalities.csv'
    municipalities(path, departement=33)
    assert len(models.Municipality.select()) == 1
    assert not len(Diff.select())


def test_create_user(monkeypatch):
    monkeypatch.setattr('ban.commands.helpers.prompt', lambda *x, **wk: 'pwd')
    assert not amodels.User.select().count()
    createuser(username='testuser', email='aaaa@bbbb.org')
    assert amodels.User.select().count() == 1
    user = amodels.User.first()
    assert user.is_staff


def test_truncate_should_truncate_all_tables_by_default(monkeypatch):
    factories.MunicipalityFactory()
    factories.StreetFactory()
    monkeypatch.setattr('ban.commands.helpers.confirm', lambda *x, **wk: True)
    truncate()
    assert not models.Municipality.select().count()
    assert not models.Street.select().count()


def test_truncate_should_only_truncate_given_names(monkeypatch):
    factories.MunicipalityFactory()
    factories.StreetFactory()
    monkeypatch.setattr('ban.commands.helpers.confirm', lambda *x, **wk: True)
    truncate(names=['street'])
    assert models.Municipality.select().count()
    assert not models.Street.select().count()


def test_truncate_should_not_ask_for_confirm_in_force_mode(monkeypatch):
    factories.MunicipalityFactory()
    truncate(force=True)
    assert not models.Municipality.select().count()
