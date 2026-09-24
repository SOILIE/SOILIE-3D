"""Contact search versus an exhaustive triangle oracle; no Blender required."""
import math
import unittest
from unittest.mock import patch

import numpy as np
from modules import support_settlement as contact


def surface(triangles):
    triangles=np.asarray(triangles,dtype=float)
    lows,highs=triangles.min(axis=1),triangles.max(axis=1)
    def build(indices):
        low,high=lows[indices].min(axis=0),highs[indices].max(axis=0)
        if len(indices)<=8:
            return low,high,indices,None
        mid=len(indices)//2
        return low,high,None,(build(indices[:mid]),build(indices[mid:]))
    return {'low':lows.min(axis=0),'high':highs.max(axis=0),'triangles':triangles,
            'triangleLows':lows,'triangleHighs':highs,'projection':build(np.arange(len(triangles)))}


class ContactSearchTests(unittest.TestCase):
    def test_dense_flat_surface_stops_after_proven_minimum(self):
        upper=surface([[(0,0,1),(1,0,1),(0,1,1)]]*8192)
        lower=surface([[(-2,-2,0),(3,-2,0),(-2,3,0)]])
        with patch.object(contact,'triangle_drop',wraps=contact.triangle_drop) as narrow:
            self.assertEqual(1,contact.surface_drop(upper,lower))
            self.assertEqual(1,narrow.call_count)

    def test_random_sloped_meshes_match_exhaustive_contact(self):
        rng=np.random.default_rng(1500)
        for _ in range(80):
            upper=rng.uniform(-1,1,(13,3,3)); upper[:,:,2]+=4
            lower=rng.uniform(-1,1,(17,3,3))
            expected=min(contact.triangle_drop(a,b) for a in upper for b in lower)
            result=contact.surface_drop(surface(upper),surface(lower))
            if math.isfinite(expected):
                self.assertAlmostEqual(expected,result,places=10)
            else:
                self.assertTrue(math.isinf(result))

    def test_touching_stacked_disjoint_and_crossed_edges(self):
        base=surface([[(-2,-2,0),(2,-2,0),(0,2,0)]])
        for gap in (0,.2,3):
            upper=surface([[(-.1,-.1,gap),(.1,-.1,gap),(0,.1,gap)]])
            self.assertAlmostEqual(gap,contact.surface_drop(upper,base))
        remote=surface([[(9,9,1),(10,9,1),(9,10,1)]])
        self.assertTrue(math.isinf(contact.surface_drop(remote,base)))


if __name__=='__main__':
    unittest.main()
