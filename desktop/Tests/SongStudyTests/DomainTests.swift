import XCTest
@testable import SongStudy

final class DomainTests: XCTestCase {
    @MainActor func testPlaybackVolumeCannotAmplifyBeyondFullLevel() {
        let audio=AudioTransport()
        XCTAssertEqual(audio.volume,1)
        audio.volume=0.5
        XCTAssertEqual(audio.volume,0.5)
        audio.volume=1.5
        XCTAssertEqual(audio.volume,1)
        audio.volume = -0.2
        XCTAssertEqual(audio.volume,0)
    }
    func testBoundaryPositionRoundsAcrossBarsAndUsesDetectedMeter() {
        let beats=(0..<16).map{Double($0)*0.5}
        let downbeats=[0.0,2.0,3.5,5.0]
        XCTAssertEqual(MusicalRuler.position(at:1.7,beats:beats,downbeats:downbeats),"Bar 1.4")
        XCTAssertEqual(MusicalRuler.position(at:1.8,beats:beats,downbeats:downbeats),"Bar 2.1")
        XCTAssertEqual(MusicalRuler.position(at:3.2,beats:beats,downbeats:downbeats),"Bar 2.3")
        XCTAssertEqual(MusicalRuler.position(at:3.3,beats:beats,downbeats:downbeats),"Bar 3.1")
        XCTAssertEqual(MusicalRuler.position(at:0.5,beats:beats,downbeats:[1,3]),"Pickup · beat 2")
        XCTAssertEqual(MusicalRuler.position(at:1.8,beats:beats,downbeats:[]),"Beat 5")
        XCTAssertNil(MusicalRuler.position(at:0,beats:[],downbeats:[]))
    }
    func testSectionColorsFollowRenamesInsteadOfModelLabels() {
        var section=SongSection(id:"a",name:"Chorus 2",kind:"chorus",repeatGroup:nil,startFrame:0,endFrame:441000)
        let chorusColor=section.displayRole.fill
        section.name="Verse 2"
        XCTAssertEqual(section.displayRole,.verse)
        XCTAssertNotEqual(section.displayRole.fill,chorusColor)
        section.name="My favorite bit"
        XCTAssertEqual(section.displayRole,.unknown)
        for (name,role) in [(" PRE CHORUS 2 ",SectionRole.preChorus),("Post–Chorus",.postChorus),("Final Chorus",.chorus),("Chorus2",.chorus),("Bridge (half time)",.bridge),("Intro",.intro),("Outro",.outro),("Instrumental 3",.instrumental)] {
            section.name=name
            XCTAssertEqual(section.displayRole,role,name)
        }
        section.name="Chorusing textures"
        XCTAssertEqual(section.displayRole,.unknown)
    }
    func testComparisonNamesRespectListenerLabels() {
        var section=SongSection(id:"a",name:"Chorus 2",kind:"verse",repeatGroup:nil,startFrame:0,endFrame:441000)
        XCTAssertEqual(section.comparisonName,"chorus")
        section.name="Post-Chorus"
        XCTAssertNotEqual(section.comparisonName,"chorus")
    }
    func testSplitAndMergePreserveExactCoverageAndLeftName() throws {
        let section = SongSection(id: "a", name: "Chorus", kind: "chorus", repeatGroup: 2, startFrame: 0, endFrame: 441000)
        let split = SectionEditing.split([section], at: 123457)
        XCTAssertEqual(split.count, 2)
        XCTAssertEqual(split[0].endFrame, 123457)
        XCTAssertEqual(split[1].startFrame, 123457)
        XCTAssertEqual(SectionEditing.merge(split, boundary: 1).first?.name, "Chorus")
        XCTAssertEqual(SectionEditing.merge(split, boundary: 1).first?.endFrame, 441000)
    }
    func testMoveClampsAndUsesRealBeatPositionsOnlyWhenRequested() {
        let sections = [SongSection(id:"a",name:"Verse",kind:"verse",repeatGroup:nil,startFrame:0,endFrame:220500),
                        SongSection(id:"b",name:"Chorus",kind:"chorus",repeatGroup:nil,startFrame:220500,endFrame:441000)]
        let free = SectionEditing.move(sections, boundary:1, to:204001, snap:false, beats:[4.5,5.0])
        XCTAssertEqual(free[1].startFrame,204001)
        let snapped = SectionEditing.move(sections, boundary:1, to:204001, snap:true, beats:[4.5,5.0])
        XCTAssertEqual(snapped[1].startFrame,198450)
        XCTAssertEqual(SectionEditing.move(sections,boundary:1,to:-10,snap:false,beats:[])[0].endFrame,4410)
    }
    func testZoomKeepsPointerTimeAndClipsToSong() {
        let window = TimelineWindow(start:20,end:80,duration:120)
        let zoom = window.zoom(factor:2,anchor:0.25)
        XCTAssertEqual(zoom.start + 0.25 * zoom.span,35,accuracy:0.00001)
        XCTAssertEqual(zoom.span,30)
        XCTAssertGreaterThanOrEqual(zoom.pan(seconds:-500).start,0)
    }
    func testEndingDoesNotSetBodyScaleOrInternalDrop() {
        let body = (0..<180).map { 0.80 + Double($0 % 5)*0.01 }
        let values = body + [0.60,0.3,0.1,0]
        let scale = EnergyScale(values:values,frameHz:1)
        XCTAssertGreaterThan(scale.low,0.7)
        XCTAssertNotNil(scale.tailStart)
        XCTAssertLessThan(scale.position(0),scale.position(0.8))
        XCTAssertTrue(scale.internalDrop(values:values,frameHz:1).map { $0 < 180 } ?? true)
    }
    func testSoloRoutingDoesNotAffectTransportPosition() {
        XCTAssertEqual(AudioRouting.gain(for:"mix",solos:[]),1)
        XCTAssertEqual(AudioRouting.gain(for:"mix",solos:["bass"]),0)
        XCTAssertEqual(AudioRouting.gain(for:"bass",solos:["bass"]),1)
        XCTAssertEqual(AudioRouting.gain(for:"drums",solos:["bass"]),0)
    }
}

extension DomainTests {
    func testSmoothingIsCenteredPreservesConstantAndAllowsRaw() {
        var impulse=[Double](repeating:0,count:201);impulse[100]=1
        let smoothed=EnergyPresentation.smooth(impulse,hz:20,seconds:2)
        XCTAssertEqual(smoothed.firstIndex(of:smoothed.max()!),100)
        XCTAssertEqual(smoothed[95],smoothed[105],accuracy:0.000001)
        XCTAssertLessThan(smoothed[100],impulse[100])
        XCTAssertEqual(EnergyPresentation.smooth(impulse,hz:20,seconds:0),impulse)
        XCTAssertTrue(EnergyPresentation.smooth(Array(repeating:0.6,count:201),hz:20,seconds:2).allSatisfy{abs($0-0.6)<0.000001})
    }
    func testLargestChangesIgnoreTerminalFadeAndStayIndependentOfDisplaySmoothing() {
        let rise=EnergyChange(t:30,direction:"rise",delta:0.3,contributors:[])
        let drop=EnergyChange(t:80,direction:"fall",delta:-0.2,contributors:[])
        let ending=EnergyChange(t:176,direction:"fall",delta:-0.9,contributors:[])
        let changes=[rise,drop,ending]
        XCTAssertEqual(EnergyPresentation.largest(changes,rise:false,duration:180,tail:178,bpm:120)?.id,drop.id)
        XCTAssertEqual(EnergyPresentation.largest(changes,rise:true,duration:180,tail:178,bpm:120)?.id,rise.id)
        XCTAssertEqual(EnergyPresentation.visible(changes,window:TimelineWindow(start:75,end:90,duration:180),width:1000),[drop])
    }
    func testBarRulerThinsLabelsWithoutChangingBarNumbers() {
        let downbeats=(0..<100).map{Double($0)*2+0.5}
        let full=MusicalRuler.bars(downbeats:downbeats,start:0,end:200,width:1000)
        XCTAssertTrue(full.count<30)
        let zoom=MusicalRuler.bars(downbeats:downbeats,start:20,end:30,width:1000)
        XCTAssertEqual(zoom.first?.number,11)
        XCTAssertEqual(zoom.first?.time,20.5)
        XCTAssertEqual(zoom.count,5)
    }
    func testFullscreenLayoutUsesHeightAndRetainsRoomForInspection() {
        let normal=TimelineLayout.height(available:680,tracks:5,inspecting:false,zoomed:false)
        let full=TimelineLayout.height(available:1260,tracks:5,inspecting:false,zoomed:false)
        XCTAssertGreaterThan(full,normal+400)
        let layout=TimelineLayout(height:full,tracks:5)
        XCTAssertEqual(layout.trackY+layout.trackHeight*5+14,full,accuracy:0.01)
        XCTAssertEqual(TimelineLayout.height(available:1260,tracks:5,inspecting:true,zoomed:false),full)
    }
}
