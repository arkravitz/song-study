import SwiftUI
import AppKit

@MainActor final class SongStudyAppDelegate:NSObject,NSApplicationDelegate {
    weak var store:StudyStore?
    func applicationDidFinishLaunching(_ notification:Notification) {
        if let icon=Bundle.main.url(forResource:"SongStudy",withExtension:"icns"),let image=NSImage(contentsOf:icon) {
            NSApp.applicationIconImage=image
        }
    }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender:NSApplication) -> Bool {true}
    func applicationWillTerminate(_ notification:Notification) {
        store?.audio.pause()
        store?.worker.terminate()
    }
}

@main struct SongStudyApp:App {
    @NSApplicationDelegateAdaptor(SongStudyAppDelegate.self) private var appDelegate
    @StateObject private var store=StudyStore()
    var body:some Scene {
        WindowGroup("Song Study") {
            WorkspaceView(store:store,audio:store.audio)
                .frame(minWidth:880,minHeight:620)
                .preferredColorScheme(.dark)
                .onAppear {appDelegate.store=store}
        }
        .defaultSize(width:1280,height:850)
        .commands {
            CommandGroup(replacing:.newItem) {Button("Add song…"){store.addSong()}.keyboardShortcut("o").disabled(store.busy)}
            CommandGroup(replacing:.undoRedo) {
                Button("Undo") {
                    if let editor=NSApp.keyWindow?.firstResponder as? NSTextView,editor.isFieldEditor {editor.undoManager?.undo()}else{store.undo()}
                }.keyboardShortcut("z").disabled(!(store.song?.canUndo ?? false) && store.renaming==nil)
                Button("Redo") {
                    if let editor=NSApp.keyWindow?.firstResponder as? NSTextView,editor.isFieldEditor {editor.undoManager?.redo()}else{store.undo(true)}
                }.keyboardShortcut("z",modifiers:[.command,.shift]).disabled(!(store.song?.canRedo ?? false) && store.renaming==nil)
            }
            CommandMenu("Song") {
                Button("Play / Pause"){store.audio.toggle()}.keyboardShortcut(.space,modifiers:[])
                Button("Split at playhead"){store.split()}.keyboardShortcut("e")
                Button("Loop current section"){store.loopSection()}.keyboardShortcut("l")
                Button("Rename selected section"){store.renaming=store.selected}
                Divider()
                Button("Zoom in"){store.zoom(2)}.keyboardShortcut("+")
                Button("Zoom out"){store.zoom(0.5)}.keyboardShortcut("-")
                Button("Whole song"){store.wholeSong()}.keyboardShortcut("0",modifiers:[])
            }
        }
    }
}

struct WorkspaceView:View {
    @ObservedObject var store:StudyStore
    @ObservedObject var audio:AudioTransport
    var body:some View {
        VStack(spacing:0) {
            header
            Divider().opacity(0.4)
            if let song=store.song {
                toolbar(song)
                GeometryReader {workspace in
                VStack(spacing:0) {
                    if store.reference != nil {
                        overview(song).padding(.top,12)
                        ScrollView(.vertical,showsIndicators:false) {
                            InspectorView(store:store,audio:audio,availableHeight:workspace.size.height).padding(.horizontal,28).padding(.vertical,22)
                        }
                    } else {
                        timeline(song,height:TimelineLayout.height(available:workspace.size.height,tracks:store.visibleTracks.count,inspecting:store.selected != nil || store.selectedTrack != nil || store.selectedEnergy != nil,zoomed:store.window.span<song.duration-0.1))
                        if store.window.span < song.duration-0.1 {overview(song)}
                        ScrollView(.vertical,showsIndicators:false) {
                            if store.selected != nil || store.selectedTrack != nil || store.selectedEnergy != nil {InspectorView(store:store,audio:audio,availableHeight:workspace.size.height).padding(.horizontal,28).padding(.vertical,22)}
                            else {listeningFooter(song)}
                        }
                    }
                }
                .background(Color(red:0.078,green:0.09,blue:0.11))
                }
            }else {
                Spacer()
                Image(systemName:"waveform").font(.system(size:46,weight:.light)).foregroundStyle(.secondary)
                Text("Understand a song by listening.").font(.title2).padding(.top,16)
                Text("Sections, energy, and separated parts. All on this Mac.").foregroundStyle(.secondary).padding(.top,6)
                Button("Add a song…"){store.addSong()}.buttonStyle(.borderedProminent).padding(.top,20).disabled(store.busy)
                Button("Open library"){store.libraryOpen=true}.buttonStyle(.plain).padding(12)
                Spacer()
            }
            if store.busy {
                HStack(spacing:12) {
                    ProgressView().controlSize(.small)
                    Text(store.message).font(.system(size:12)).lineLimit(1)
                    Spacer()
                    Button("Stop"){store.cancelAnalysis()}.buttonStyle(.borderless)
                    Text("Audio stays on this Mac").foregroundStyle(.secondary).font(.system(size:11))
                }.padding(.horizontal,24).padding(.vertical,12).background(.ultraThinMaterial)
            }
        }
        .background(Color(red:0.078,green:0.09,blue:0.11))
        .sheet(isPresented:$store.libraryOpen){LibraryView(store:store)}
        .alert("Couldn't finish that action",isPresented:Binding(get:{store.error != nil},set:{if !$0 {store.error=nil}})) {
            Button("OK"){store.error=nil}
        } message:{Text(store.error ?? "")}
    }
    var header:some View {
        HStack(spacing:18) {
            Button {store.libraryOpen=true} label:{Image(systemName:"sidebar.left")}.buttonStyle(.plain).help("Open song library")
            VStack(alignment:.leading,spacing:3) {
                Text(store.song?.title ?? "Song Study").font(.system(size:16,weight:.semibold)).lineLimit(1)
                if let artist=store.song?.artist,!artist.isEmpty {Text(artist).font(.system(size:11)).foregroundStyle(.secondary).lineLimit(1)}
            }.frame(maxWidth:330,alignment:.leading)
            Spacer(minLength:15)
            Button {audio.seek(max(0,audio.position-5))} label:{Image(systemName:"gobackward.5")}.buttonStyle(.plain).help("Back five seconds")
            Button {audio.toggle()} label:{Image(systemName:audio.playing ? "pause.fill":"play.fill").font(.system(size:16)).frame(width:40,height:40).background(Color.white.opacity(0.13),in:Circle())}.buttonStyle(.plain).help("Play / Pause · Space").disabled(store.song==nil)
            Button {audio.seek(audio.position+5)} label:{Image(systemName:"goforward.5")}.buttonStyle(.plain).help("Forward five seconds")
            VStack(alignment:.leading,spacing:3) {
                Text("\(songTime(audio.position)) / \(songTime(store.song?.duration ?? 0))").font(.system(size:13,design:.monospaced)).foregroundStyle(.primary)
                Text(store.current?.name ?? " ").font(.system(size:11)).foregroundStyle(.secondary).lineLimit(1)
            }.frame(width:150,alignment:.leading)
            Spacer(minLength:15)
            if !audio.solos.isEmpty {
                Button("Hear full song"){audio.clearSolos()}.font(.system(size:11)).buttonStyle(.borderless)
            }
            HStack(spacing:9) {
                Image(systemName:audio.volume==0 ? "speaker.slash":audio.volume<0.5 ? "speaker.wave.1":"speaker.wave.2")
                    .foregroundStyle(.secondary).frame(width:18).accessibilityHidden(true)
                Slider(value:$audio.volume,in:0...1).frame(width:100).accessibilityLabel("Playback volume")
                    .help("Playback volume · 0–100%")
                Text("\(Int((audio.volume*100).rounded()))%")
                    .font(.system(size:12,design:.monospaced)).monospacedDigit()
                    .foregroundStyle(.secondary).frame(width:40,alignment:.trailing)
            }
        }.padding(.horizontal,24).padding(.vertical,18)
    }
    func toolbar(_ song:StudySong) -> some View {
        HStack(spacing:15) {
            Button("Split"){store.split()}.help("Split at the playhead · Cmd-E").disabled(store.editing)
            Button(audio.loop==nil ? "Loop":"Stop loop"){store.loopSection()}.help("Loop the selected or current section")
            Button("Compare"){if store.selected==nil{store.selected=store.current?.id};store.compare()}.disabled(song.sections.count<2)
            Divider().frame(height:15)
            Toggle("Snap",isOn:$store.snap).toggleStyle(.checkbox).font(.system(size:11)).help("Snap boundary edits to beats. Shift-drag moves freely. Ordinary seeking does not snap.")
            Button {store.undo()} label:{Image(systemName:"arrow.uturn.backward")}.disabled(!song.canUndo || store.editing).help("Undo · Cmd-Z")
            Button {store.undo(true)} label:{Image(systemName:"arrow.uturn.forward")}.disabled(!song.canRedo || store.editing).help("Redo · Shift-Cmd-Z")
            Spacer()
            Button("Energy"){store.energySettingsOpen.toggle()}.popover(isPresented:$store.energySettingsOpen,arrowEdge:.bottom){EnergySettingsView(store:store)}.help("Adjust curve smoothing and energy change markers")
            Menu("Sections") {
                ForEach(song.sections) {section in Button("\(section.name) · \(songTime(section.start))"){store.inspect(section)}}
            }.menuStyle(.borderlessButton).frame(width:85)
            Text(song.bpm>0 ? "\(Int(song.bpm.rounded())) BPM":"No beat grid").font(.system(size:11)).foregroundStyle(.secondary).help("Estimated tempo. Snapping uses the detected beat timestamps.")
            Button {store.zoom(0.5)} label:{Image(systemName:"minus")}.help("Zoom out")
            Button {store.zoom(2)} label:{Image(systemName:"plus")}.help("Zoom in")
            Button("Whole song"){store.wholeSong()}
        }.buttonStyle(.borderless).font(.system(size:12)).padding(.horizontal,24).padding(.vertical,11)
    }
    func timeline(_ song:StudySong,height:Double) -> some View {
        TimelineHost(store:store,audio:audio)
            .frame(height:height)
            .overlay(alignment:.topLeading) {
                GeometryReader {geometry in
                    let layout=TimelineLayout(height:height,tracks:store.visibleTracks.count)
                    if let section=song.sections.first(where:{$0.id==store.renaming}) {
                        let left=max(136,136+(section.start-store.window.start)/store.window.span*(geometry.size.width-154))
                        SectionNameEditor(store:store,section:section).frame(width:min(220,max(150,(section.end-section.start)/store.window.span*(geometry.size.width-154)-14)),height:30).offset(x:left+5,y:layout.sectionY+7)
                    }else if let section=store.inspected {
                        let right=min(geometry.size.width-18,136+(section.end-store.window.start)/store.window.span*(geometry.size.width-154))
                        if right>=160 {
                            Button {store.renaming=section.id} label:{Image(systemName:"pencil").font(.system(size:10)).padding(6).background(Color.black.opacity(0.4),in:RoundedRectangle(cornerRadius:3))}.buttonStyle(.plain).help("Rename \(section.name)").offset(x:right-25,y:layout.sectionY+10)
                        }
                    }
                }
            }
    }
    func overview(_ song:StudySong) -> some View {
        GeometryReader {g in
            ZStack(alignment:.leading) {
                Rectangle().fill(Color.white.opacity(0.035))
                HStack(spacing:1) {ForEach(song.sections){s in Rectangle().fill(s.kind=="chorus" ? Color.green.opacity(0.22):Color.white.opacity(0.09)).frame(width:max(1,(s.end-s.start)/song.duration*g.size.width))}}
                Rectangle().stroke(Color.white.opacity(0.5),lineWidth:1).background(Color.white.opacity(0.08)).frame(width:store.window.span/song.duration*g.size.width).offset(x:store.window.start/song.duration*g.size.width)
            }.contentShape(Rectangle()).gesture(DragGesture(minimumDistance:0).onChanged{v in
                let t=max(0,min(song.duration,Double(v.location.x/g.size.width)*song.duration))
                let a=max(0,min(song.duration-store.window.span,t-store.window.span/2))
                store.window=TimelineWindow(start:a,end:a+store.window.span,duration:song.duration)
            })
        }.frame(height:22).padding(.leading,136).padding(.trailing,18).padding(.vertical,10).help("Drag to navigate the song")
    }
    func listeningFooter(_ song:StudySong) -> some View {
        HStack(alignment:.top,spacing:30) {
            VStack(alignment:.leading,spacing:7) {
                Text(audio.position<0.5 ? "Opening parts":"Playing here").font(.system(size:11)).foregroundStyle(.secondary)
                let active=store.visibleTracks.filter{track in audio.position<0.5 ? track.participation(from:0,to:2)>0.25:track.segments.contains{audio.position>=$0.start && audio.position<$0.end}}
                Text(active.isEmpty ? "Listen to explore this passage":active.map(\.name).joined(separator:" · ")).font(.system(size:14)).foregroundStyle(.primary)
            }
            Spacer()
            HStack(spacing:28) {
                ForEach([true,false],id:\.self) {rise in
                    if let change=store.largestEnergy(rise:rise) {
                        Button {store.inspectEnergy(change)} label:{
                            VStack(alignment:.trailing,spacing:6) {
                                Text(rise ? "Biggest energy rise":"Biggest internal drop").font(.system(size:11)).foregroundStyle(.secondary)
                                Text("\(rise ? "↑":"↓") \(songTime(change.t)) · \(song.sections.first{change.t >= $0.start && change.t < $0.end}?.name ?? "Listen")").font(.system(size:13))
                            }
                        }.buttonStyle(.plain)
                    }
                }
            }
        }.padding(.horizontal,28).padding(.top,20).padding(.bottom,24)
        .overlay(alignment:.bottomLeading) {
            if song.tracks.contains(where:{$0.quiet}) {
                Button(store.showQuiet ? "Hide quiet parts":"Show additional quiet parts"){store.showQuiet.toggle()}.buttonStyle(.plain).font(.system(size:11)).foregroundStyle(.secondary).padding(.leading,28).offset(y:14)
            }
        }
    }
}

struct SectionNameEditor:View {
    @ObservedObject var store:StudyStore
    let section:SongSection
    @State private var name=""
    @FocusState private var focused:Bool
    var body:some View {
        TextField("Section name",text:$name).textFieldStyle(.plain).foregroundStyle(.white).font(.system(size:13,weight:.medium)).padding(7).background(Color(red:0.18,green:0.21,blue:0.26),in:RoundedRectangle(cornerRadius:3)).overlay(RoundedRectangle(cornerRadius:3).stroke(Color.white.opacity(0.65))).focused($focused)
            .onAppear{name=section.name;focused=true}
            .onSubmit{store.rename(section.id,name:name);store.renaming=nil}
            .onExitCommand{store.renaming=nil}
            .accessibilityLabel("Rename \(section.name)")
    }
}

struct LibraryView:View {
    @ObservedObject var store:StudyStore
    var body:some View {
        VStack(alignment:.leading,spacing:20) {
            HStack{Text("Your songs").font(.title2.weight(.semibold));Spacer();Button("Done"){store.libraryOpen=false}}
            ScrollView {
                VStack(spacing:0) {
                    ForEach(store.library) {song in
                        Button {Task{await store.open(song.id)}} label:{HStack{VStack(alignment:.leading,spacing:5){Text(song.title).font(.system(size:15,weight:.medium));Text(song.artist).font(.system(size:12)).foregroundStyle(.secondary)};Spacer();Text(songTime(song.duration)).foregroundStyle(.secondary);Image(systemName:"chevron.right").font(.system(size:10))}.contentShape(Rectangle()).padding(.vertical,14)}.buttonStyle(.plain).disabled(store.busy)
                        Divider()
                    }
                }
            }
            HStack{Text("Songs and analysis stay on this Mac.").font(.system(size:11)).foregroundStyle(.secondary);Spacer();Button("Add song…"){store.addSong()}.disabled(store.busy)}
        }.padding(28).frame(width:580,height:440)
    }
}

struct EnergySettingsView:View {
    @ObservedObject var store:StudyStore
    var body:some View {
        VStack(alignment:.leading,spacing:16) {
            Text("Energy display").font(.system(size:16,weight:.semibold))
            Toggle("Adapt smoothing to zoom",isOn:$store.smoothingAuto).toggleStyle(.checkbox)
            HStack {
                Text("Smoothing").foregroundStyle(.secondary)
                Spacer()
                Text(store.energyWindow<0.04 ? "Raw":String(format:"%.1f seconds",store.energyWindow)).monospacedDigit()
            }
            Slider(value:Binding(get:{store.energyWindow},set:{store.smoothingAuto=false;store.smoothingSeconds=$0}),in:0...4,step:0.1).accessibilityLabel("Energy smoothing in seconds")
            HStack {Text("More detail");Spacer();Text("Smoother shape")}.font(.system(size:11)).foregroundStyle(.secondary)
            Toggle("Show rises and drops",isOn:$store.showEnergyChanges).toggleStyle(.checkbox)
            Text("Smoothing changes the display only. The analysis and change timings stay the same.").font(.system(size:11)).foregroundStyle(.secondary).fixedSize(horizontal:false,vertical:true)
            Button("Reset to automatic"){store.smoothingAuto=true;store.showEnergyChanges=true}.buttonStyle(.borderless)
        }.font(.system(size:12)).padding(22).frame(width:310)
    }
}
