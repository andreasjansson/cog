import sys

import warnings
from cog._vendor import charset_normalizer as chardet
warnings.filterwarnings("ignore", "Trying to detect", module="charset_normalizer")

# This code exists for backwards compatibility reasons.
# I don't like it either. Just look the other way. :)

for package in ("urllib3", "idna"):
    vendored_package = "cog._vendor." + package
    locals()[package] = __import__(vendored_package)
    # This traversal is apparently necessary such that the identities are
    # preserved (requests.packages.urllib3.* is urllib3.*)
    for mod in list(sys.modules):
        if mod == vendored_package or mod.startswith(vendored_package + '.'):
            unprefixed_mod = mod[len("cog._vendor."):]
            sys.modules['cog._vendor.requests.packages.' + unprefixed_mod] = sys.modules[mod]

# Kinda cool, though, right?
