from . import models
from . import wizard


def reactivate_official_document_types(env):
    """Without this module, the types of Odoo are the only ones left."""
    for code in ("31", "32", "33", "34"):
        document_type = env.ref("l10n_do.ecf_%s" % code, raise_if_not_found=False)
        if document_type:
            document_type.active = True
