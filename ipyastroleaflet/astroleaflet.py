from ipyastroleaflet.leaflet import *
from ipywidgets import *
from traitlets import Unicode, dlink, link, Dict, Undefined
import pymongo
import pandas as pd
import numpy as np
from pandas import DataFrame, Series
import uuid
from notebook.utils import url_path_join
import requests
import json


class AstroMap(Map):

    @default('layout')
    def _default_layout(self):
        return Layout(height='512px', width='512px')

    scroll_wheel_zoom = Bool(True).tag(sync=True, o=True)
    wheel_debounce_time = Int(60).tag(sync=True, o=True)
    wheel_px_per_zoom_level = Int(60).tag(sync=True, o=True)
    zoom = Int(1).tag(sync=True, o=True)
    max_zoom = Int(12).tag(sync=True, o=True)
    position_control = Bool(True).tag(sync=True, o=True)
    fullscreen_control = Bool(True).tag(sync=True, o=True)
    _des_crs = List().tag(sync=True)
    pan_loc = List().tag(sync=True)
    # pan_ready = Bool(False).tag(sync=True)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        if self.default_tiles is not None:
            self._des_crs = self.default_tiles._des_crs
            self.max_zoom = self.default_tiles.max_zoom
            if self.center == [0.0, 0.0]:
                self.center = self.default_tiles.center

    def add_layer(self, layer):
        if layer.model_id in self.layer_ids:
            raise LayerException('layer already on map: %r' % layer)
        layer._map = self
        if isinstance(layer, GridLayer):
            self._des_crs = layer._des_crs
            self.max_zoom = layer.max_zoom
            self.center = layer.center
        self.layers = tuple([l for l in self.layers] + [layer])
        layer.visible = True

    def remove_layer(self, layer):
        if layer.model_id not in self.layer_ids:
            raise LayerException('layer not on map: %r' % layer)
        # if isinstance(layer, GridLayer):
        #     self._des_crs = [0, 0, 1, 1]
        self.layers = tuple([l for l in self.layers if l.model_id != layer.model_id])
        layer.visible = False

    def clear_layers(self):
        self.layers = ()

    def center_map(self):
        center = []
        try:
            center.append(self._des_crs[1]-128*self._des_crs[3])
            center.append(self._des_crs[0]+128*self._des_crs[2])
            self.fly_to(center, 1)
        except:
            print('No base tiles added!')

    def fly_to(self, latlng, zoom):
        latlng.append(zoom)
        self.pan_loc = latlng
        self.pan_loc = []


class GridLayer(RasterLayer):
    _view_name = Unicode('LeafletGridLayerView').tag(sync=True)
    _model_name = Unicode('LeafletGridLayerModel').tag(sync=True)

    bottom = Bool(False).tag(sync=True)
    _des_crs = List().tag(Sync=True)
    df = Instance(DataFrame, allow_none=True)
    min_zoom = Int(0).tag(sync=True, o=True)
    max_zoom = Int(8).tag(sync=True, o=True)
    # tile_size = Int(256).tag(sync=True, o=True)
    detect_retina = Bool(False).tag(sync=True, o=True)
    collection = Unicode().tag(sync=True, o=True)
    x_range = Float(1.0).tag(sync=True, o=True)
    y_range = Float(1.0).tag(sync=True, o=True)
    color = Unicode('red').tag(sync=True, o=True)
    center = List().tag(sync=True)
    obj_catalog = Instance(Series, allow_none=True)

    _popup_callbacks = Instance(CallbackDispatcher, ())

    def __init__(self, connection, coll_name=None, **kwargs):
        super().__init__(**kwargs)
        try:
            self.db = connection.db
        except:
            raise Exception('Mongodb connection error! Check connection object!')

        self.coll_name = coll_name
        self._server_url = connection._url
        self._checkInput()
        self.push_data(self._server_url)
        self._popup_callbacks.register_callback(self._query_obj, remove=False)
        self.on_msg(self._handle_leaflet_event)

        print('Mongodb collection name is {}'.format(self.collection))

    def _checkInput(self):
        if not self.collection == '':
            meta = self.db[self.collection].find_one({'_id': 'meta'})
            self._des_crs = meta['adjust']
            self.x_range = meta['xRange']
            self.y_range = meta['yRange']
        elif self.df is not None:
            clms = [x.upper() for x in list(self.df.columns)]

            if not set(['RA', 'DEC']).issubset(set(clms)):
                raise Exception("RA, DEC is required for visualization!")
            if not set(['A_IMAGE', 'B_IMAGE', 'THETA_IMAGE']).issubset(set(clms)):
                print('Without data for the object shape, every object will appear as a point')

            df_r, self._des_crs = self._data_prep(self.max_zoom, self.df)
            self.x_range = self._des_crs[2]*256
            self.y_range = self._des_crs[3]*256
            self._insert_data(df_r)
        else:
            raise Exception('Need to provide a collection name or a pandas dataframe!')

    def _data_prep(self, zoom, df):
        dff = df.copy()
        (xMax, xMin) = (dff['RA'].max(), dff['RA'].min())
        (yMax, yMin) = (dff['DEC'].max(), dff['DEC'].min())
        scaleMax = 2**int(zoom)
        x_range = xMax - xMin
        y_range = yMax - yMin

        dff['tile_x'] = ((dff.RA-xMin)*scaleMax/x_range).apply(np.floor).astype(int)
        dff['tile_y'] = ((yMax-dff.DEC)*scaleMax/y_range).apply(np.floor).astype(int)
        dff.loc[:, 'a'] = dff.loc[:, 'A_IMAGE'].apply(lambda x: x*0.267/3600)
        dff.loc[:, 'b'] = dff.loc[:, 'B_IMAGE'].apply(lambda x: x*0.267/3600)
        dff['zoom'] = int(zoom)

        xScale = x_range/256
        yScale = y_range/256
        return dff, [xMin, yMax, xScale, yScale]

    def _insert_data(self, df):
        if self.coll_name is not None:
            self.collection = self.coll_name
        if self.collection == '':
            coll_id = str(uuid.uuid4())
            self.collection = coll_id

        data_d = df.to_dict(orient='records')
        coll = self.db[self.collection]
        coll.insert_many(data_d, ordered=False)
        coll.insert_one({'_id': 'meta', 'adjust': self._des_crs, 'xRange': self.x_range, 'yRange': self.y_range})

    def push_data(self, url):

        mRange = (self.x_range + self.y_range)/2
        self.center = [self._des_crs[1]-self.y_range/2, self._des_crs[0]+self.x_range/2]
        body = {
            'collection': self.collection,
            'mrange': mRange
        }
        push_url = url_path_join(url, '/rangeinfo/')
        req = requests.post(push_url, data=body)

    def _handle_leaflet_event(self, _, content, buffers):
        if content.get('event', '') == 'popup: click':
            self._popup_callbacks(**content)

    def _query_obj(self, **kwargs):
        body = {'coll': self.collection,'RA': kwargs['RA'], 'DEC': kwargs['DEC']}
        popup_url = url_path_join(self._server_url, '/objectPop/')
        result = requests.get(popup_url, data=body)
        pop_dict = json.loads(result.text[1:-1])
        self.obj_catalog = Series(pop_dict)