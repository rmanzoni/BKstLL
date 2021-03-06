import ROOT
from itertools import product, combinations
import math

from PhysicsTools.Heppy.analyzers.core.Analyzer      import Analyzer
from PhysicsTools.Heppy.analyzers.core.AutoHandle    import AutoHandle
from PhysicsTools.HeppyCore.utils.deltar             import deltaR, deltaR2, bestMatch
from PhysicsTools.Heppy.physicsobjects.Muon          import Muon
from PhysicsTools.Heppy.physicsobjects.Electron      import Electron
from PhysicsTools.Heppy.physicsobjects.PhysicsObject import PhysicsObject

from CMGTools.BKstLL.analyzers.utils import isAncestor, displacement2D, displacement3D, makeRecoVertex # utility functions
from CMGTools.BKstLL.physicsobjects.BKLL import BKLL


from pdb import set_trace

##########################################################################################
# load custom library to ROOT. This contains the kinematic vertex fitter class
ROOT.gSystem.Load('libCMGToolsBKstLL')
from ROOT import KinematicVertexFitter as VertexFitter

class BKJPsiAnalyzer(Analyzer):
    '''
    '''
    def declareHandles(self):
        super(BKJPsiAnalyzer, self).declareHandles()

        self.handles['electrons' ] = AutoHandle('slimmedElectrons'                           , 'std::vector<pat::Electron>'                    )
        self.handles['muons'     ] = AutoHandle('slimmedMuons'                               , 'std::vector<pat::Muon>'                        )
        self.handles['losttracks'] = AutoHandle('lostTracks'                                 , 'std::vector<pat::PackedCandidate>'             )
        self.handles['pfcands'   ] = AutoHandle('packedPFCandidates'                         , 'std::vector<pat::PackedCandidate>'             )
        self.handles['pvs'       ] = AutoHandle(('offlineSlimmedPrimaryVertices', '', 'PAT' ), 'std::vector<reco::Vertex>'                     )
        self.handles['beamspot'  ] = AutoHandle(('offlineBeamSpot'              , '', 'RECO'), 'reco::BeamSpot'                                )


    def beginLoop(self, setup):
        super(BKJPsiAnalyzer, self).beginLoop(setup)
        self.counters.addCounter('BKJPsiAnalyzer')
        count = self.counters.counter('BKJPsiAnalyzer')
        count.register('all events')
        count.register('>= 2 muons')
        count.register('dimuon mass < 6')
        count.register('good dimuon vtx')
        count.register('B(KLL) mass < 6')

        # stuff I need to instantiate only once
        self.vtxfit = VertexFitter()
        # create a std::vector<reco::RecoChargedCandidate> to be passed to the fitter 
        self.tofit_cc = ROOT.std.vector('reco::RecoChargedCandidate')()
        # create a std::vector<pat::PackedCandidate> to be passed to the fitter 
        self.tofit_pc = ROOT.std.vector('pat::PackedCandidate')()

    def process(self, event):
        self.readCollections(event.input)

        self.counters.counter('BKJPsiAnalyzer').inc('all events')

        # vertex stuff
        event.pvs         = self.handles['pvs'     ].product()
        event.beamspot    = self.handles['beamspot'].product()

        # get the tracks
        allpf      = map(PhysicsObject, self.handles['pfcands'   ].product())
        losttracks = map(PhysicsObject, self.handles['losttracks'].product())

        # merge the track collections
        event.alltracks = sorted([tt for tt in allpf + losttracks if tt.charge() != 0 and abs(tt.pdgId()) not in (11,13)], key = lambda x : x.pt(), reverse = True)
        
        # get the offline electrons and muons
        event.electrons = map(Electron, self.handles['electrons'].product())
        event.muons     = map(Muon    , self.handles['muons'    ].product())
        
        # preselect muons
        event.muons = [muon for muon in event.muons if self.testMuon(muon)]
        
        if len(event.muons)<2: return False
        self.counters.counter('BKJPsiAnalyzer').inc('>= 2 muons')

        # build all di-muon pairs
        dimuons = [(mu1, mu2) for mu1, mu2 in combinations(event.muons, 2)]

        # opposite sign di-muon
        dimuons = [(mu1, mu2) for mu1, mu2 in dimuons if mu1.charge() != mu2.charge()]

        # SAVE THEM ALL, ALSO NON RESONANT!
        # invariant mass
        dimuons = [(mu1, mu2) for mu1, mu2 in dimuons if (mu1.p4() + mu2.p4()).mass() < 6.]
        if not len(dimuons): return False
        self.counters.counter('BKJPsiAnalyzer').inc('dimuon mass < 6')
        
        selDimuons = []
        
        # dimuons with a good vertex
        for dimuon in dimuons:
            # clear the vectors
            self.tofit_cc.clear()
            self.tofit_pc.clear()
            # create a RecoChargedCandidate for each reconstructed lepton and flush it into the vector
            for il in dimuon:
                # if the reco particle is a displaced thing, it does not have the p4() method, so let's build it 
                myp4 = ROOT.Math.LorentzVector('<ROOT::Math::PxPyPzE4D<double> >')(il.px(), il.py(), il.pz(), math.sqrt(il.mass()**2 + il.px()**2 + il.py()**2 + il.pz()**2))
                ic = ROOT.reco.RecoChargedCandidate() # instantiate a dummy RecoChargedCandidate
                ic.setCharge(il.charge())             # assign the correct charge
                ic.setP4(myp4)                        # assign the correct p4
                ic.setTrack(il.track())               # set the correct TrackRef
                if ic.track().isNonnull():            # check that the track is valid
                    self.tofit_cc.push_back(ic)

            # further sanity check: two *distinct* tracks
            if self.tofit_cc.size()==2 and self.tofit_cc[0].track() != self.tofit_cc[1].track():
                # fit it!
                svtree = self.vtxfit.Fit(self.tofit_cc, self.tofit_pc) # actual vertex fitting
                # check that the vertex is good
                if not svtree.get().isEmpty() and svtree.get().isValid():
                    svtree.movePointerToTheTop()
                    sv = svtree.currentDecayVertex().get()
                    recoSv = makeRecoVertex(sv, kinVtxTrkSize=2) # need to do some gymastics
                    selDimuons.append(dimuon)
        
        if len(selDimuons)==0: return False
        self.counters.counter('BKJPsiAnalyzer').inc('good dimuon vtx')

        cands = []
        
        # add a track
        for dimu, tk in product(selDimuons, event.alltracks):
            # remove double countings 
            if deltaR(tk, dimu[0])<0.01 or deltaR(tk, dimu[1])<0.01:
                continue
            # pt and sanity checks on the track
#             set_trace()
            if not tk.bestTrack():
                continue
            if tk.pt()<0.8:
                continue
            # clear the vectors
            self.tofit_cc.clear()
            self.tofit_pc.clear()
            # create a RecoChargedCandidate for each reconstructed lepton and flush it into the vector
            for il in dimu:
                # if the reco particle is a displaced thing, it does not have the p4() method, so let's build it 
                myp4 = ROOT.Math.LorentzVector('<ROOT::Math::PxPyPzE4D<double> >')(il.px(), il.py(), il.pz(), math.sqrt(il.mass()**2 + il.px()**2 + il.py()**2 + il.pz()**2))
                ic = ROOT.reco.RecoChargedCandidate() # instantiate a dummy RecoChargedCandidate
                ic.setCharge(il.charge())             # assign the correct charge
                ic.setP4(myp4)                        # assign the correct p4                
                ic.setTrack(il.track())               # set the correct TrackRef
                if ic.track().isNonnull():            # check that the track is valid
                    self.tofit_cc.push_back(ic)
            
            set_trace()
            m_k = 0.493677
            # push the track into the vector
            self.tofit_pc.push_back(tk.physObj)

            # fit it!
            try:
                svtree = self.vtxfit.Fit(self.tofit_cc, self.tofit_pc) # actual vertex fitting
            except:
                set_trace()
            # check that the vertex is good
            if not svtree.get().isEmpty() and svtree.get().isValid():
                svtree.movePointerToTheTop()
                sv = svtree.currentDecayVertex().get()
                recoSv = makeRecoVertex(sv, kinVtxTrkSize=3) # need to do some gymastics
                cands.append(BKLL(dimu[0], dimu[1], tk, recoSv, event.beamspot))

        if not len(cands): return False
        self.counters.counter('BKJPsiAnalyzer').inc('good dimuon vtx')

        cands = [cand for cand in cands if cand.b().mass()<6]
        if not len(cands): return False
        self.counters.counter('BKJPsiAnalyzer').inc('B(KLL) mass < 6')

#         set_trace()
#         for jj in cands: print jj.b().mass(), jj.b().pt(), jj.b().eta(), jj.b().phi(), jj.ll().mass(), jj.ls2d(), jj.vtxprob(), jj.llcone(), jj.bcone()

        event.myB = sorted(cands, key = lambda x : x.vtxprob(), reverse=True)[0]
        
        return True
    
    def testMuon(self, muon):
        return muon.pt()>1.        and \
               abs(muon.eta())<2.5 and \
               muon.isGlobalMuon()
    
    
    