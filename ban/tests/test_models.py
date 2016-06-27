import peewee
import pytest

from ban.core import models
from ban.core.versioning import Version

from .factories import (GroupFactory, HouseNumberFactory,
                        MunicipalityFactory, PositionFactory, PostCodeFactory)


def test_get_model_locks_version():
    m = MunicipalityFactory()
    municipality = models.Municipality.get(models.Municipality.pk == m.pk)
    assert municipality._locked_version == 1


def test_select_first_model_locks_version():
    MunicipalityFactory()
    municipality = models.Municipality.select().first()
    assert municipality._locked_version == 1


def test_municipality_is_created_with_version_1():
    municipality = MunicipalityFactory()
    assert municipality.version == 1


def test_municipality_is_versioned():
    municipality = MunicipalityFactory(name="Moret-sur-Loing")
    assert len(municipality.versions) == 1
    assert municipality.version == 1
    municipality.name = "Orvanne"
    municipality.increment_version()
    municipality.save()
    assert municipality.version == 2
    assert len(municipality.versions) == 2
    version1 = municipality.versions[0].load()
    version2 = municipality.versions[1].load()
    assert version1.name == "Moret-sur-Loing"
    assert version2.name == "Orvanne"
    assert municipality.versions[0].diff
    diff = municipality.versions[1].diff
    assert len(diff.diff) == 1  # name, version
    assert diff.diff['name']['new'] == "Orvanne"
    municipality.insee = "77316"
    municipality.increment_version()
    municipality.save()
    assert len(municipality.versions) == 3
    diff = municipality.versions[2].diff
    assert diff.old == municipality.versions[1]
    assert diff.new == municipality.versions[2]


def test_municipality_diff_contain_only_changed_data():
    municipality = MunicipalityFactory(name="Moret-sur-Loing", insee="77316")
    municipality.name = "Orvanne"
    # "Changed" with same value.
    municipality.insee = "77316"
    municipality.increment_version()
    municipality.save()
    diff = municipality.versions[1].diff
    assert len(diff.diff) == 1  # name, version
    assert 'insee' not in diff.diff
    assert diff.diff['name']['new'] == "Orvanne"


def test_municipality_postcodes():
    municipality = MunicipalityFactory(name="Paris")
    postcode1 = PostCodeFactory(code="75010", municipality=municipality)
    postcode2 = PostCodeFactory(code="75011", municipality=municipality)
    postcodes = municipality.postcodes
    assert len(postcodes) == 2
    assert postcode1 in postcodes
    assert postcode2 in postcodes


def test_municipality_as_resource():
    municipality = MunicipalityFactory(name="Montbrun-Bocage", insee="31365",
                                       siren="210100566")
    PostCodeFactory(code="31310", municipality=municipality)
    assert municipality.as_resource['name'] == "Montbrun-Bocage"
    assert municipality.as_resource['insee'] == "31365"
    assert municipality.as_resource['siren'] == "210100566"
    assert municipality.as_resource['version'] == 1
    assert municipality.as_resource['id'] == municipality.id
    assert municipality.as_resource['postcodes'] == ['31310']


def test_municipality_as_relation():
    municipality = MunicipalityFactory(name="Montbrun-Bocage", insee="31365",
                                       siren="210100566")
    PostCodeFactory(code="31310", municipality=municipality)
    assert municipality.as_relation['name'] == "Montbrun-Bocage"
    assert municipality.as_relation['insee'] == "31365"
    assert municipality.as_relation['siren'] == "210100566"
    assert municipality.as_relation['id'] == municipality.id
    assert 'postcodes' not in municipality.as_relation
    assert 'version' not in municipality.as_relation


def test_municipality_str():
    municipality = MunicipalityFactory(name="Salsein")
    assert str(municipality) == 'Salsein'


def test_save_should_be_rollbacked_if_version_save_fails():
    municipality = MunicipalityFactory()
    assert Version.select().count() == 1
    # Artificially create a version
    municipality.increment_version()
    municipality.store_version()
    assert Version.select().count() == 2
    with pytest.raises(peewee.IntegrityError):
        municipality.save()
    assert Version.select().count() == 2
    models.Municipality.select().count() == 1


@pytest.mark.parametrize('factory,kwargs', [
    (MunicipalityFactory, {'insee': '12345'}),
    (MunicipalityFactory, {'siren': '123456789'}),
])
def test_unique_fields(factory, kwargs):
    factory(**kwargs)
    with pytest.raises(peewee.IntegrityError):
        factory(**kwargs)


def test_should_allow_deleting_municipality_not_linked():
    municipality = MunicipalityFactory()
    municipality.delete_instance()
    assert not models.Municipality.select().count()


def test_should_not_allow_deleting_municipality_linked_to_street():
    municipality = MunicipalityFactory()
    GroupFactory(municipality=municipality)
    with pytest.raises(peewee.IntegrityError):
        municipality.delete_instance()
    assert models.Municipality.get(models.Municipality.id == municipality.id)


def test_group_is_versioned():
    initial_name = "Rue des Pommes"
    street = GroupFactory(name=initial_name)
    assert street.version == 1
    street.name = "Rue des Poires"
    street.increment_version()
    street.save()
    assert street.version == 2
    assert len(street.versions) == 2
    version1 = street.versions[0].load()
    version2 = street.versions[1].load()
    assert version1.name == "Rue des Pommes"
    assert version2.name == "Rue des Poires"
    assert street.versions[0].diff
    diff = street.versions[1].diff
    assert len(diff.diff) == 1  # name, version
    assert diff.diff['name']['new'] == "Rue des Poires"


def test_should_allow_deleting_street_not_linked():
    street = GroupFactory()
    street.delete_instance()
    assert not models.Group.select().count()


def test_should_not_allow_deleting_street_linked_to_housenumber():
    street = GroupFactory()
    HouseNumberFactory(parent=street)
    with pytest.raises(peewee.IntegrityError):
        street.delete_instance()
    assert models.Group.get(models.Group.id == street.id)


def test_tmp_fantoir_should_use_name():
    municipality = MunicipalityFactory(insee='93031')
    street = GroupFactory(municipality=municipality, fantoir='',
                          name="Rue des Pêchers")
    assert street.tmp_fantoir == '#RUEDESPECHERS'


def test_compute_cia_should_consider_insee_fantoir_number_and_ordinal():
    municipality = MunicipalityFactory(insee='93031')
    street = GroupFactory(municipality=municipality, fantoir='930311491')
    hn = HouseNumberFactory(parent=street, number="84", ordinal="bis")
    hn = models.HouseNumber.get(models.HouseNumber.id == hn.id)
    assert hn.compute_cia() == '93031_1491_84_BIS'


def test_compute_cia_should_let_ordinal_empty_if_not_set():
    municipality = MunicipalityFactory(insee='93031')
    street = GroupFactory(municipality=municipality, fantoir='930311491')
    hn = HouseNumberFactory(parent=street, number="84", ordinal="")
    assert hn.compute_cia() == '93031_1491_84_'


def test_compute_cia_should_use_locality_if_no_street():
    municipality = MunicipalityFactory(insee='93031')
    street = GroupFactory(municipality=municipality, fantoir='930311491')
    hn = HouseNumberFactory(parent=street, number="84", ordinal="")
    assert hn.compute_cia() == '93031_1491_84_'


def test_group_as_list():
    municipality = MunicipalityFactory()
    street = GroupFactory(municipality=municipality, name="Rue des Fleurs",
                          fantoir="930311491")
    data = street.as_list
    assert data == {
        'id': street.id,
        'municipality': municipality.id,
        'kind': 'way',
        'fantoir': '930311491',
        'alias': None,
        'ign': None,
        'name': 'Rue des Fleurs',
        'resource': 'group',
        'attributes': None,
        'laposte': None,
        'created_at': street.created_at,
        'created_by': street.created_by.pk,
        'modified_at': street.modified_at,
        'modified_by': street.modified_by.pk,
    }


def test_housenumber_should_create_cia_on_save():
    municipality = MunicipalityFactory(insee='93031')
    street = GroupFactory(municipality=municipality, fantoir='930311491')
    hn = HouseNumberFactory(parent=street, number="84", ordinal="bis")
    assert hn.cia == '93031_1491_84_BIS'


def test_housenumber_is_versioned():
    street = GroupFactory()
    hn = HouseNumberFactory(parent=street, ordinal="b")
    assert hn.version == 1
    hn.ordinal = "bis"
    hn.increment_version()
    hn.save()
    assert hn.version == 2
    assert len(hn.versions) == 2
    version1 = hn.versions[0].load()
    version2 = hn.versions[1].load()
    assert version1.ordinal == "b"
    assert version2.ordinal == "bis"
    assert version2.parent == street


def test_get_postcode_housenumbers_sorted():
    postcode = PostCodeFactory()
    hn2 = HouseNumberFactory(postcode=postcode, number="2", ordinal="")
    hn2ter = HouseNumberFactory(postcode=postcode, number="2", ordinal="ter")
    hn1 = HouseNumberFactory(postcode=postcode, number="1", ordinal="")
    hn2bis = HouseNumberFactory(postcode=postcode, number="2", ordinal="bis")
    assert postcode.housenumbers == [hn1, hn2, hn2bis, hn2ter]


def test_get_group_housenumbers_parent_sorted():
    group = GroupFactory()
    hn1a = HouseNumberFactory(parent=group, number="1", ordinal="A")
    hn2ter = HouseNumberFactory(parent=group, number="2", ordinal="ter")
    hn1 = HouseNumberFactory(parent=group, number="1", ordinal="")
    hn2bis = HouseNumberFactory(parent=group, number="2", ordinal="bis")
    assert group.housenumbers == [hn1, hn1a, hn2bis, hn2ter]


def test_get_group_housenumbers_ancestor_sorted():
    group = GroupFactory()
    hn1a = HouseNumberFactory(ancestors=group, number="1", ordinal="A")
    hn2ter = HouseNumberFactory(ancestors=group, number="2", ordinal="ter")
    hn1 = HouseNumberFactory(ancestors=group, number="1", ordinal="")
    hn2bis = HouseNumberFactory(ancestors=group, number="2", ordinal="bis")
    assert group.housenumbers == [hn1, hn1a, hn2bis, hn2ter]


def test_get_group_housenumbers_parent_ancestor_sorted():
    group = GroupFactory()
    hn1a = HouseNumberFactory(parent=group, number="1", ordinal="A")
    hn2ter = HouseNumberFactory(parent=group, number="2", ordinal="ter")
    hn1 = HouseNumberFactory(ancestors=group, number="1", ordinal="")
    hn2bis = HouseNumberFactory(ancestors=group, number="2", ordinal="bis")
    assert group.housenumbers == [hn1, hn1a, hn2bis, hn2ter]


def test_cannot_duplicate_housenumber_on_same_street():
    street = GroupFactory()
    HouseNumberFactory(parent=street, ordinal="b", number="10")
    with pytest.raises(peewee.IntegrityError):
        HouseNumberFactory(parent=street, ordinal="b", number="10")


def test_cannot_create_housenumber_without_parent():
    with pytest.raises(peewee.DoesNotExist):
        HouseNumberFactory(parent=None)


def test_housenumber_str():
    hn = HouseNumberFactory(ordinal="b", number="10")
    assert str(hn) == '10 b'


def test_can_create_two_housenumbers_with_same_number_but_different_streets():
    street = GroupFactory()
    street2 = GroupFactory()
    HouseNumberFactory(parent=street, ordinal="b", number="10")
    HouseNumberFactory(parent=street2, ordinal="b", number="10")


def test_housenumber_center():
    housenumber = HouseNumberFactory()
    position = PositionFactory(housenumber=housenumber)
    assert housenumber.center == position.center_resource


def test_housenumber_center_without_position():
    housenumber = HouseNumberFactory()
    assert housenumber.center is None


def test_housenumber_center_with_position_without_center():
    housenumber = HouseNumberFactory()
    PositionFactory(housenumber=housenumber, name="bâtiment A", center=None)
    assert housenumber.center is None


def test_create_housenumber_with_district():
    municipality = MunicipalityFactory()
    district = GroupFactory(municipality=municipality, kind=models.Group.AREA)
    housenumber = HouseNumberFactory(ancestors=[district],
                                     street__municipality=municipality)
    assert district in housenumber.ancestors
    assert housenumber in district.housenumbers


def test_add_district_to_housenumber():
    housenumber = HouseNumberFactory()
    district = GroupFactory(municipality=housenumber.parent.municipality,
                            kind=models.Group.AREA)
    housenumber.ancestors.add(district)
    assert district in housenumber.ancestors
    assert housenumber in district.housenumbers


def test_remove_housenumber_ancestors():
    municipality = MunicipalityFactory()
    district = GroupFactory(municipality=municipality, kind=models.Group.AREA)
    housenumber = HouseNumberFactory(ancestors=[district],
                                     street__municipality=municipality)
    assert district in housenumber.ancestors
    housenumber.ancestors.remove(district)
    assert district not in housenumber.ancestors


def test_should_allow_deleting_housenumber_not_linked():
    housenumber = HouseNumberFactory()
    housenumber.delete_instance()
    assert not models.HouseNumber.select().count()


def test_should_not_allow_deleting_housenumber_not_linked():
    housenumber = HouseNumberFactory()
    PositionFactory(housenumber=housenumber)
    with pytest.raises(peewee.IntegrityError):
        housenumber.delete_instance()
    assert models.HouseNumber.get(models.HouseNumber.id == housenumber.id)


def test_housenumber_as_resource():
    housenumber = HouseNumberFactory(number="90", ordinal="bis",
                                     attributes={"source": "openbar"},
                                     parent__municipality__insee="21892",
                                     parent__fantoir="218921234")
    assert housenumber.as_resource == {
        'ancestors': [],
        'cia': '21892_1234_90_BIS',
        'parent': housenumber.parent.as_relation,
        'center': None,
        'laposte': None,
        'ign': None,
        'attributes': {'source': 'openbar'},
        'version': 1,
        'id': housenumber.id,
        'number': '90',
        'postcode': None,
        'ordinal': 'bis',
        'created_by': housenumber.created_by.as_relation,
        'created_at': housenumber.created_at,
        'modified_by': housenumber.modified_by.as_relation,
        'modified_at': housenumber.modified_at,
    }


def test_position_is_versioned():
    housenumber = HouseNumberFactory()
    position = PositionFactory(housenumber=housenumber, center=(1, 2))
    assert position.version == 1
    position.center = (3, 4)
    position.increment_version()
    position.save()
    assert position.version == 2
    assert len(position.versions) == 2
    version1 = position.versions[0].load()
    version2 = position.versions[1].load()
    assert version1.center.geojson == {'type': 'Point', 'coordinates': (1, 2)}
    assert version2.center.geojson == {'type': 'Point', 'coordinates': (3, 4)}
    assert version2.housenumber == housenumber


def test_position_children():
    housenumber = HouseNumberFactory()
    parent = PositionFactory(housenumber=housenumber)
    child = PositionFactory(housenumber=housenumber, parent=parent)
    assert child in parent.children


def test_position_attributes():
    position = PositionFactory(attributes={'foo': 'bar'})
    assert position.attributes['foo'] == 'bar'
    assert models.Position.select().where(models.Position.attributes.contains({'foo': 'bar'})).exists()  # noqa


def test_get_instantiate_object_properly():
    original = PositionFactory()
    loaded = models.Position.get(models.Position.id == original.id)
    assert loaded.id == original.id
    assert loaded.version == original.version
    assert loaded.center == original.center
    assert loaded.housenumber == original.housenumber


@pytest.mark.parametrize('given,expected', [
    ((1, 2), (1, 2)),
    ((1.123456789, 2.987654321), (1.123456789, 2.987654321)),
    ([1, 2], (1, 2)),
    ("(1, 2)", (1, 2)),
    (None, None),
    ("", None),
])
def test_position_center_coerce(given, expected):
    position = PositionFactory(center=given, name="bâtiment Z")
    center = models.Position.get(models.Position.id == position.id).center
    if given:
        assert center.coords == expected
    else:
        assert not center
