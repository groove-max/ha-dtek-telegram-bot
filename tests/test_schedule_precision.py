"""Exact timeline positions in both Telegram image formats, with old HA support."""
from copy import deepcopy
from io import BytesIO
import unittest
from PIL import Image, ImageColor
from test_grid_members import large_grid
from schedule_image import COLORS, render_grid, timeline_segments
from schedule_pages import select_page, render_page


class SchedulePrecisionTests(unittest.TestCase):
    def data(self, start=735, end=825):
        data=large_grid(1)
        day=data['days'][0];row=day['rows'][0]
        row['home']=row['selected']=True
        row['cells']=['clear']*24+['outage']*4+['clear']*20
        row['segments']=[dict(start=0,end=start,kind='clear'),dict(start=start,end=end,kind='outage'),dict(start=end,end=1440,kind='clear')]
        return data

    def test_both_pngs_colour_exact_boundaries_not_legacy_cells(self):
        for start,end in ((735,825),(727,799)):
            data=self.data(start,end);original=deepcopy(data)
            for raw,left,scale,y,origin in ((render_grid(data),210,28/30,180,0),
                    (render_page(select_page(data)),150,670/720,294,720)):
                with Image.open(BytesIO(raw)) as image:
                    for minute,kind in ((start-4,'clear'),(start+4,'outage'),(end-4,'outage'),(end+4,'clear')):
                        self.assertEqual(image.getpixel((round(left+(minute-origin)*scale),y)), ImageColor.getrgb(COLORS[kind]), (start,end,minute))
            self.assertEqual(data,original)

    def test_old_integration_retains_its_half_hour_precision(self):
        data=self.data();day=data['days'][0];row=day['rows'][0];del row['segments']
        self.assertEqual(timeline_segments(day,row)[1],dict(start=720,end=840,kind='outage'))
        self.assertTrue(render_grid(data).startswith(b'\x89PNG'))
        self.assertTrue(render_page(select_page(data)).startswith(b'\x89PNG'))

    def test_invalid_segments_never_fall_back_to_rounded_cells(self):
        for parts in ([],[dict(start=0,end=1500,kind='clear')],
                [dict(start=0,end=float('nan'),kind='clear')],
                [dict(start=False,end=1440,kind='clear')],
                [dict(start=0,end=720,kind='clear')],
                [dict(start=0,end=1440,kind='invalid')]):
            data=self.data();data['days'][0]['rows'][0]['segments']=parts
            for render in (render_grid,lambda d:render_page(select_page(d))):
                with self.subTest(parts=parts),self.assertRaises(ValueError):render(data)

    def test_unknown_partial_coverage_is_not_replaced_with_clear(self):
        data=self.data();row=data['days'][0]['rows'][0]
        row['cells']=['unknown']*48
        row['segments']=[dict(start=0,end=735,kind='unknown'),dict(start=735,end=825,kind='clear'),dict(start=825,end=1440,kind='unknown')]
        self.assertTrue(render_grid(data))  # Exact coverage is useful even if legacy cells are unknown.
        self.assertTrue(render_page(select_page(data)))
