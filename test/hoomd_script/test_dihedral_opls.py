# -*- coding: iso-8859-1 -*-
# Maintainer: ksil

from hoomd_script import *
import hoomd_script;
context.initialize()
import unittest
import os
import numpy

# tests dihedral.harmonic
class dihedral_opls_tests (unittest.TestCase):
    def setUp(self):
        print
        snap = data.make_snapshot(N=40,
                                  box=data.boxdim(L=100),
                                  particle_types = ['A'],
                                  bond_types = [],
                                  angle_types = [],
                                  dihedral_types = ['dihedralA'],
                                  improper_types = [])

        if comm.get_rank() == 0:
            snap.dihedrals.resize(10);

            for i in range(10):
                x = numpy.array([i, 0, 0], dtype=numpy.float32)
                snap.particles.position[4*i+0,:] = x;
                x += numpy.random.random(3)
                snap.particles.position[4*i+1,:] = x;
                x += numpy.random.random(3)
                snap.particles.position[4*i+2,:] = x;
                x += numpy.random.random(3)
                snap.particles.position[4*i+3,:] = x;

                snap.dihedrals.group[i,:] = [4*i+0, 4*i+1, 4*i+2, 4*i+3];

        init.read_snapshot(snap)

        sorter.set_params(grid=8)

    # test to see that se can create an OPLS dihedral
    def test_create(self):
        dihedral.opls();

    # test setting coefficients
    def test_set_coeff(self):
        oplsdi = dihedral.opls();
        oplsdi.set_coeff('dihedralA', k1=1.0, k2=2.0, k3=3.0, k4=4.0)
        all = group.all();
        integrate.mode_standard(dt=0.005);
        integrate.nve(all);
        run(100);

    # test coefficient not set checking
    def test_set_coeff_fail(self):
        oplsdi = dihedral.opls();
        all = group.all();
        integrate.mode_standard(dt=0.005);
        integrate.nve(all);
        self.assertRaises(RuntimeError, run, 100);

    def tearDown(self):
        context.initialize();



if __name__ == '__main__':
    unittest.main(argv = ['test.py', '-v'])
