import datetime
import math
from typing import List, Optional, Tuple, Dict

import pyproj
from flask.json import jsonify

MAX_DISTANCE = 100_000  # maximum allowable error, in meters, for a projection to be considered
MAX_NUM_RESULTS = 10

# list of pyproj.Proj objects is a global for reuse by repeated invocations of cloud functions
global_projs: Optional[Dict[str, pyproj.Proj]] = {}
global_proj_wgs84 = pyproj.Proj('EPSG:4326')
global_geod_wgs84 = pyproj.Geod(ellps='WGS84')


def setup_projs(epsg_only: bool = True) -> Dict[str, pyproj.Proj]:
    """Initialize a list of pyproj.Proj objects, each of which handles one projection."""

    # By default only consider EPSG projections, ignoring ESRI and IGNF
    authorities = ['EPSG'] if epsg_only else pyproj.database.get_authorities()

    # Setting the environment variable PYPROJ_GLOBAL_CONTEXT=ON will make instantiation *much* faster, see
    # https://github.com/pyproj4/pyproj/issues/661#issuecomment-653277033 for details.
    projs = {}
    for authority in authorities:
        for code in pyproj.database.get_codes(authority, pyproj.enums.PJType.PROJECTED_CRS, allow_deprecated=True):
            try:
                projs[f'{authority}:{code}'] = pyproj.Proj(f'{authority}:{code}')
            except pyproj.exceptions.ProjError:
                pass

    return projs


def projection_search(
    lng: float, lat: float, x: float, y: float, num_results: int = MAX_NUM_RESULTS
) -> List[Tuple[float, str]]:
    """Brute force search for a projection that places (`x`, `y`) as close as possible to (`lng`, `lat`). This is
    typically used when the projection of (`x`, `y`) is unknown or was lost.

    Arguments:
    - lng, lat: longitude and latitude coordinates of the known point in WGS84
    - x, y: coordinates of the point whose CRS should project it close to (`lng`, `lat`)
    Returns:
    - List of tuples with error distance (in meters) and `authority:code` lookup.
    """

    # lazily initialize the global list of projs
    global global_projs, global_proj_wgs84  # pylint: disable=global-statement
    if not global_projs:
        start = datetime.datetime.now()
        global_projs = setup_projs()
        print(f'Initialized {len(global_projs)} projs in {(datetime.datetime.now() - start).total_seconds()} secs')

    results: List[Tuple[float, str]] = []
    start = datetime.datetime.now()
    for lookup, proj in global_projs.items():
        try:  # Unproject (x,y) to WGS84 blindly assuming its CRS is `proj`
            x_wgs84: float
            y_wgs84: float
            x_wgs84, y_wgs84 = proj(x, y, inverse=True)
        except RuntimeError:
            continue
        dist: float
        _, _, dist = global_geod_wgs84.inv(lng, lat, x_wgs84, y_wgs84)  # compute the distance to (lng, lat) in meters
        if not math.isnan(dist) and dist < MAX_DISTANCE:
            results.append((dist, lookup))

    results.sort()
    return results[:num_results]


def handler(request):
    """The flask request handler for performing these lookups via cloud function"""

    if request.method == 'OPTIONS':
        return (
            '',
            204,
            {
                'Access-Control-Allow-Origin': '*',
                'Access-Control-Allow-Methods': 'GET',
                'Access-Control-Allow-Headers': 'Content-Type',
                'Access-Control-Max-Age': '3600',
            },
        )

    try:
        lng = float(request.args['lng'])
        lat = float(request.args['lat'])
        x = float(request.args['x'])
        y = float(request.args['y'])
    except:  # pylint: disable=bare-except
        return "Required floating point query parameters: 'lng', 'lat', 'x', 'y'", 400

    global global_projs  # pylint: disable=global-statement
    response_dicts = []
    for distance, lookup in projection_search(lng, lat, x, y):
        proj = global_projs[lookup]
        response_dicts.append({'projection': lookup, 'distance': distance, 'name': proj.crs.name})

    response = jsonify(dict(projections=response_dicts))
    response.headers.set('Access-Control-Allow-Origin', '*')
    response.headers.set('Access-Control-Allow-Methods', 'GET')
    return response, 200
