from hashlib import sha256

from intake.catalog import IntakeCatalog
from intake.modes import InvalidModePackage, decode_mode_body
from intake.pack import Package, PackageIdentity


def accept_mode(catalog: IntakeCatalog, package: Package, expected: PackageIdentity) -> str:
    """Accept a V2 delivery after checking its envelope and anonymous evidence."""
    if not isinstance(package, Package) or not isinstance(expected, PackageIdentity):
        raise InvalidModePackage('a package and expected fixed identity are required')
    identity = package.identity
    if not isinstance(identity, PackageIdentity) or identity != expected:
        raise InvalidModePackage('delivery differs from expected identity')
    identifiers = {
        'package_id': identity.package_id,
        'analysis_id': identity.analysis_id,
        'bench_id': identity.bench_id,
        'revision_id': identity.revision_id,
    }
    if any(not isinstance(value, str) or not value.strip() for value in identifiers.values()):
        raise InvalidModePackage('package identifiers must be nonempty text')
    try:
        for value in identifiers.values():
            value.encode('utf-8')
    except UnicodeEncodeError as exc:
        raise InvalidModePackage('package identifiers must be encodable text') from exc
    body = decode_mode_body(package.body)
    if sha256(package.body).hexdigest() != identity.digest:
        raise InvalidModePackage('body digest does not match the delivery identity')
    if any(getattr(body, field) != value for field, value in identifiers.items()):
        raise InvalidModePackage('body identifiers do not match the delivery identity')
    return catalog.bind(package).accepted_id
