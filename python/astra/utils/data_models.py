import re

__dsi_path_descriptors = dict([
    # $APOGEE_REDUX/{apred}/stars/{telescope}/{field}/{prefix}Star-{apred}-{obj}.fits
    ("apStar", r".+\/(?P<apred>[\w\d]+)\/stars\/(?P<telescope>\w{3}\d{1,2}m)\/\w+\/(?P<prefix>[\w\d]+)Star-(?P<_apred>[\w\d]+)-(?P<obj>[\w\d\+\-]+)\.fits"),
])


def parse_descriptors(path):
    r"""
    Parse the data model descriptors from the given path. For example, the path

    $APOGEE_REDUX/{apred}/stars/{telescope}/{field}/{prefix}Star-{apred}-{obj}.fits
    
    describes an apStar data model file of an observation of an object, taken with a particular
    telescope, towards some field, etc. This will parse a given path and return those data model 
    descriptors.

    :param path:
        A local path to a SDSS data product that has a data model registered with the SDSS Data
        Specification Index.

    :returns:
        A two-length tuple containing the name of the matched data model, and a dictionary that
        contains the matched descriptors.

    :raises ValueError:
        If no data model could be found that describes the given path, or multiple data models were
        matched.
    """

    matches = dict()
    for name, pattern in __dsi_path_descriptors.items():
        try:
            matches[name] = re.compile(pattern).search(path).groupdict()

        except AttributeError:
            pass

    if not len(matches):
        raise ValueError("no data model found that describe the given path")

    if len(matches) > 1:
        raise ValueError(f"multiple data model matches found: {matches}")

    return matches.popitem()


def parse_data_model(path):
    r"""
    Return the SDSS data model that describes the given path.

    :param path:
        A local path to a SDSS data product that has a data model registered with the SDSS Data
        Specification Index.

    :returns:
        The name of the matched data model.

    :raises ValueError:
        If no data model could be found that describes the given path, or multiple data models were
        matched.
    """
    return parse_descriptors(path)[0]



if __name__ == "__main__":

    paths = [
        "/uufs/chpc.utah.edu/common/home/sdss/apogeework/apogee/spectro/redux/r12/stars/apo25m/M67/apStar-r12-2M08485930+1117220.fits"
    ]

    for path in paths:
        foo = parse_descriptors(path)
        print(path, foo)
