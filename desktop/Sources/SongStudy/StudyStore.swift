import AppKit
import SwiftUI

@MainActor final class StudyStore: ObservableObject {
    @Published var library:[LibrarySong]=[]
    @Published var song:StudySong? {didSet{energyCacheKey="";scaleCache=nil}}
    @Published var selected:String?
    @Published var selectedTrack:String?
    @Published var reference:String?
    @Published var window=TimelineWindow(start:0,end:180,duration:180)
    @Published var busy=false
    @Published var editing=false
    @Published var message="Opening library"
    @Published var fraction=0.0
    @Published var error:String?
    @Published var libraryOpen=false
    @Published var evidence=false
    @Published var snap=true
    @Published var showQuiet=false
    @Published var energySettingsOpen=false
    @Published var selectedEnergy:EnergyChange?
    @Published var smoothingAuto=UserDefaults.standard.object(forKey:"energySmoothingAuto") as? Bool ?? true {didSet{UserDefaults.standard.set(smoothingAuto,forKey:"energySmoothingAuto")}}
    @Published var smoothingSeconds=UserDefaults.standard.object(forKey:"energySmoothingSeconds") as? Double ?? 0.7 {didSet{UserDefaults.standard.set(smoothingSeconds,forKey:"energySmoothingSeconds")}}
    @Published var showEnergyChanges=UserDefaults.standard.object(forKey:"showEnergyChanges") as? Bool ?? true {didSet{UserDefaults.standard.set(showEnergyChanges,forKey:"showEnergyChanges")}}
    @Published var renaming:String?
    @Published var chosenSong:String?
    private var canceled=false
    let worker=WorkerClient()
    let audio=AudioTransport()
    init() {
        worker.progress={ [weak self] message,fraction in self?.message=message;self?.fraction=fraction }
        Task {await refreshLibrary(openFirst:true)}
    }
    var visibleTracks:[SongTrack] {song?.tracks.filter{showQuiet || !$0.quiet} ?? []}
    var inspected:SongSection? {song?.sections.first{$0.id==selected}}
    var current:SongSection? {song?.sections.first{audio.position >= $0.start && audio.position < $0.end} ?? song?.sections.last}
    var compared:SongSection? {song?.sections.first{$0.id==reference}}
    func refreshLibrary(openFirst:Bool=false) async {
        if openFirst {busy=true;message="Opening library"}
        do {
            library=try await worker.request("library",as:[LibrarySong].self)
            if openFirst {
                let id=UserDefaults.standard.string(forKey:"lastSong")
                if let first=library.first(where:{$0.id==id}) ?? library.first {await open(first.id)}
                else {message="Add a song to get started";libraryOpen=true;busy=false}
            }
        } catch {self.error=error.localizedDescription;if openFirst {busy=false}}
    }
    func open(_ id:String) async {
        chosenSong=id;busy=true;message="Opening song";fraction=0
        do {
            let loaded=try await worker.request("load",params:["song":id],as:StudySong.self)
            guard chosenSong==id else{return}
            try audio.load(loaded)
            song=loaded;selectedEnergy=nil;selected=nil;selectedTrack=nil;reference=nil;evidence=false
            window=TimelineWindow(start:0,end:loaded.duration,duration:loaded.duration)
            UserDefaults.standard.set(id,forKey:"lastSong")
            libraryOpen=false;busy=false
        }catch {if chosenSong==id {self.error=error.localizedDescription;busy=false}}
    }
    func addSong() {
        let panel=NSOpenPanel();panel.allowsMultipleSelection=false
        panel.allowedContentTypes=[.audio];panel.message="Choose a finished song to study. Audio stays on this Mac."
        if panel.runModal() == .OK,let url=panel.url {
            busy=true;message="Opening audio";fraction=0;canceled=false
            Task {
                struct Prepared:Decodable {let song:String;let existing:Bool}
                struct Imported:Decodable {let song:String}
                do {
                    let prepared=try await worker.request("prepare",params:["path":url.path],as:Prepared.self)
                    await refreshLibrary();await open(prepared.song)
                    if prepared.existing || canceled {return}
                    busy=true;message="Finding sections and separating parts"
                    _=try await worker.request("import",params:["path":url.path],as:Imported.self)
                    let completed=try await worker.request("load",params:["song":prepared.song],as:StudySong.self)
                    if song?.id==prepared.song {
                        try audio.addPreparedTracks(completed)
                        song=completed
                    }
                    await refreshLibrary();busy=false
                }catch {if !canceled {self.error=error.localizedDescription};busy=false}
            }
        }
    }
    func cancelAnalysis() {
        canceled=true;chosenSong=nil;worker.terminate();busy=false;message="Analysis stopped. Your song and edits are saved."
    }
    func inspect(_ section:SongSection,play:Bool=true) {
        selectedEnergy=nil;selected=section.id;selectedTrack=nil;reference=nil;evidence=false
        if play {audio.seek(section.start,autoplay:true)}
    }
    func inspectTrack(_ track:SongTrack) {selectedEnergy=nil;selectedTrack=track.id;selected=nil;reference=nil;evidence=false}
    private var scaleCache:EnergyScale?
    var energyScale:EnergyScale {
        if let scaleCache {return scaleCache}
        let scale=EnergyScale(values:song?.energy ?? [],frameHz:song?.energyHz ?? 20);scaleCache=scale;return scale
    }
    private var energyCacheKey=""
    private var energyCache=[Double]()
    var displayEnergy:[Double] {
        guard let song else{return []}
        let key="\(song.id)-\(song.energy.count)-\(energyWindow)"
        if key != energyCacheKey {energyCache=EnergyPresentation.smooth(song.energy,hz:song.energyHz,seconds:energyWindow);energyCacheKey=key}
        return energyCache
    }
    func largestEnergy(rise:Bool) -> EnergyChange? {
        guard let song else{return nil}
        let tail=energyScale.tailStart
        return EnergyPresentation.largest(song.energyChanges,rise:rise,duration:song.duration,tail:tail,bpm:song.bpm)
    }
    var energyWindow:Double {smoothingAuto ? window.span/220:smoothingSeconds}
    func inspectEnergy(_ change:EnergyChange) {
        selectedEnergy=change;selected=nil;selectedTrack=nil;reference=nil;evidence=false
        audio.seek(change.t,autoplay:true)
        if change.t<window.start || change.t>window.end {
            let a=max(0,min(window.duration-window.span,change.t-window.span/2))
            window=TimelineWindow(start:a,end:a+window.span,duration:window.duration)
        }
    }
    func split() {
        guard let song,!editing else{return}
        let frame=Int64((audio.position*songSampleRate).rounded())
        let changed=SectionEditing.split(song.sections,at:frame)
        if changed != song.sections {save(changed);selected=changed.first{$0.startFrame==frame}?.id}
    }
    func merge(_ boundary:Int) {guard let song else{return};save(SectionEditing.merge(song.sections,boundary:boundary));selected=nil}
    func rename(_ id:String,name:String) {
        guard var sections=song?.sections,let i=sections.firstIndex(where:{$0.id==id}),!name.trimmingCharacters(in:.whitespacesAndNewlines).isEmpty else{return}
        sections[i].name=String(name.trimmingCharacters(in:.whitespacesAndNewlines).prefix(80));save(sections)
    }
    func save(_ sections:[SongSection]) {
        guard let current=song,!editing,sections != current.sections else{return}
        let id=current.id
        editing=true
        Task {
            do {
                let encoded=try JSONEncoder().encode(sections)
                let value=try JSONSerialization.jsonObject(with:encoded)
                let result=try await worker.request("save",params:["song":id,"sections":value],as:EditResult.self)
                if song?.id==id {song?.sections=result.sections;song?.canUndo=result.canUndo;song?.canRedo=result.canRedo}
            }catch {self.error=error.localizedDescription}
            editing=false
        }
    }
    func undo(_ redo:Bool=false) {
        guard let id=song?.id,!editing else{return}
        editing=true
        Task {
            do {
                let result=try await worker.request(redo ? "redo":"undo",params:["song":id],as:EditResult.self)
                if song?.id==id {song?.sections=result.sections;song?.canUndo=result.canUndo;song?.canRedo=result.canRedo;selected=nil;reference=nil}
            }catch {self.error=error.localizedDescription}
            editing=false
        }
    }
    func compare() {
        guard let section=inspected,let song else{return}
        // Listener names take precedence over model families after corrections.
        let named=song.sections.filter{$0.id != section.id && $0.comparisonName==section.comparisonName}
        let repeated=named.isEmpty ? song.sections.filter{$0.id != section.id && (($0.kind==section.kind && section.kind != "unknown") || ($0.repeatGroup != nil && $0.repeatGroup==section.repeatGroup))} : named
        reference=(repeated.last(where:{$0.start<section.start}) ?? repeated.first ?? song.sections.last(where:{$0.start<section.start}))?.id
    }
    func zoom(_ factor:Double,anchor:Double=0.5){window=window.zoom(factor:factor,anchor:anchor)}
    func wholeSong(){if let song {window=TimelineWindow(start:0,end:song.duration,duration:song.duration)}}
    func loopSection() {
        if audio.loop != nil {audio.loop=nil;return}
        if let section=inspected ?? current {audio.loop=section.start...section.end;audio.seek(section.start,autoplay:true)}
    }
    func switchComparison(to section:SongSection) {
        guard let inspected else{return}
        let origin=audio.position>=inspected.start && audio.position<inspected.end ? inspected:compared
        let elapsed=origin.map{max(0,audio.position-$0.start)} ?? 0
        audio.loop=section.start...section.end
        audio.seek(section.start + (elapsed<section.end-section.start ? elapsed:0),autoplay:true)
    }
}
