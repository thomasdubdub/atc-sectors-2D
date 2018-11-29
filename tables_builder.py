import requests
from bs4 import BeautifulSoup
import numpy as np
import pandas as pd
import geopandas as gpd
from shapely.geometry import Point, Polygon, MultiPoint, MultiPolygon
from shapely.ops import nearest_points
from collections import defaultdict

world = gpd.read_file(gpd.datasets.get_path('naturalearth_lowres'))
france = world[world.name == "France"].copy()
france.geometry = france.geometry.intersection(Polygon([(-10,41),(-10,52),(10,52),(10,41)]))
poly = france.geometry.iloc[0][1]
mp = MultiPoint(poly.exterior.coords)
fr = list(mp.geoms)

def get_points_between(before_point, after_point):
    b_n = nearest_points(mp, before_point)[0]
    a_n = nearest_points(mp, after_point)[0]
    tlist = [point for point in fr 
             if min(fr.index(b_n), fr.index(a_n)) <= fr.index(point) <= max(fr.index(b_n), fr.index(a_n))]
    clist = [point for point in fr if point not in tlist]
    tlist = clist if len(tlist) > len(clist) else tlist
    flist = tlist if fr.index(b_n) <= fr.index(a_n) else tlist[::-1]
    if len(flist) >= 3:
        flist = flist[1:-1]
    elif len(flist) >= 2:
        flist = flist[1:]
    return flist

def lat_conv(slat):
    val = round(float(slat[0:2]) + float(slat[3:5])/60 + float(slat[6:8])/3600, 3)
    return val if slat[9] == 'N' else -val

def lon_conv(slon):
    val = round(float(slon[1:3]) + float(slon[4:6])/60 + float(slon[7:9])/3600, 3)
    return val if slon[10] == 'E' else -val

def table_to_df(table, name, vol_es):
    vol_dict = defaultdict(list)
    es_dict = defaultdict(set)
    cs = ""
    for tag in table.find_all('span'):
        if tag.has_attr('class'):
            cs = tag.text
        else:
            vol_dict[cs].append(tag.text)
            es_dict[cs].add(vol_es[tag.text])
    es_dict = {key: list(value) for key, value in es_dict.items()}
    cs = [*vol_dict]
    df = pd.DataFrame({'control_sector': cs, 'acc':[name for i in range(len(cs))]})
    df['volumes'] = df['control_sector'].map(vol_dict)
    df['elementary_sectors'] = df['control_sector'].map(es_dict)
    return df

def get_tables(url):
    soup = BeautifulSoup(requests.get(url).content, "lxml")
    list_tables = soup.find_all('table')
    cdict = defaultdict(str)
    vol_es, es_acc, upper, lower = ({} for i in range(4))
    vol, es = ('', '')
    latest_lat, latest_long, current_lat = (0.0, 0.0, 0.0)
    boundary_required = False

    for tag in list_tables[5].find_all('span'):
        if tag.has_attr('id'):
            if 'NOM_USUEL' in tag['id']:
                acc = tag.text
            elif 'AIRSPACE.TXT_NAME' in tag['id']:
                es = tag.text
                es_acc[es] = acc
                vol = es
                vol_es[vol] = es
            elif 'DIST_VER_UPPER' in tag['id']:
                upper[vol] = tag.text
            elif 'DIST_VER_LOWER' in tag['id']:
                lower[vol] = tag.text
            elif 'GEO_LAT' in tag['id']:
                if tag.text[0].isdigit():
                    lat = lat_conv(tag.text)
                    if boundary_required:
                        current_lat = lat
                    else:
                        latest_lat = lat
            elif 'GEO_LONG' in tag['id']:
                if tag.text[0].isdigit():
                    lon = lon_conv(tag.text)
                    if boundary_required:
                        ch = get_points_between(Point(latest_long, latest_lat), Point(lon, current_lat))
                        for pt in ch:
                            cdict[vol] += str(pt.y) + ";" + str(pt.x) + ","
                        cdict[vol] += str(current_lat) + ";" + str(lon) + ","
                        boundary_required = False
                    else:
                        latest_long = lon
                        cdict[vol] += str(latest_lat) + ";" + str(lon) + ","
            elif 'GEO_BORDER.NOM' in tag['id']:
                boundary_required = True
            elif 'AIRSPACE_BORDER.NOM_PARTIE' in tag['id']:
                if (len(tag.text) == 1) and (tag.text[0].isdigit()):
                    vol_es.pop(vol)
                    vol += " " + tag.text[0]
                    vol_es[vol] = es
        else: # second column
            if (len(tag.text) == 1) and (tag.text[0].isdigit()):
                    vol_es.pop(vol)
                    vol += " " + tag.text[0]
                    vol_es[vol] = es

    fdict = defaultdict(list)
    for key, value in cdict.items():
        for couple in value.split(","):
            if len(couple) > 0:
                fdict[key].append((float(couple.split(";")[1]), float(couple.split(";")[0])))
    city_acc_map = {'BORDEAUX':'LFBB', 'BREST':'LFRR', 'MARSEILLE':'LFMM', 'PARIS':'LFFF', 'REIMS':'LFEE'}
    es_acc = {key: city_acc_map[value] for key,value in es_acc.items()}
    fdict = {key: Polygon(value) for key, value in fdict.items()}
    df_v = pd.DataFrame({'volume': [*fdict]}, dtype=str)
    df_v['elementary_sector'] = df_v['volume'].map(vol_es)
    df_v['acc'] = df_v['elementary_sector'].map(es_acc)
    df_v['level_min'] = df_v['volume'].map(lower)
    df_v['level_max'] = df_v['volume'].map(upper)
    df_v['geometry'] = df_v['volume'].map(fdict)
    f = lambda x: 0 if x == 'SFC' else 999 if x == 'UNL' else int(x[3:]) # to be modified with real SFC/UNL values
    df_v[['level_min','level_max']] = df_v[['level_min','level_max']].applymap(f)
    gdf_es = gpd.GeoDataFrame(df_v, geometry='geometry')
    # Building the control sectors dataframe
    list_acc = ['LFBB', 'LFRR', 'LFMM', 'LFFF', 'LFEE']
    lfbb, lfrr, lfmm, lfff, lfee = (table_to_df(list_tables[i], list_acc[i], vol_es) for i in range(5))
    acc = pd.concat([lfbb, lfrr, lfmm, lfff, lfee])
    return (gdf_es, acc)