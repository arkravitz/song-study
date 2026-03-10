import SwiftUI

struct InspectorView:View {
    @ObservedObject var store:StudyStore
    @ObservedObject var audio:AudioTransport
    var availableHeight:Double=700
    var comparisonRowHeight:Double {min(100,28+max(0,availableHeight-760)/Double(max(1,store.visibleTracks.count)))}
    var body:some View {
        VStack(alignment:.leading,spacing:16) {
            Divider().opacity(0.5)
            if let song=store.song,let change=store.selectedEnergy {energyDetail(song,change)}
            else if let song=store.song,let section=store.inspected {
                if let reference=store.compared {comparison(song,section,reference)}
                else {sectionDetail(song,section)}
            }else if let song=store.song,let track=song.tracks.first(where:{$0.id==store.selectedTrack}) {
                HStack(alignment:.top) {
                    VStack(alignment:.leading,spacing:8) {
                        Text(track.name).font(.system(size:23,weight:.semibold))
                        Text("A separated part of the recording. Listen to verify quiet passages.").font(.system(size:13)).foregroundStyle(.secondary)
                        if let first=track.segments.first {
                            Button("First detected entrance · \(songTime(first.start))"){audio.seek(max(0,first.start-1),autoplay:true)}.buttonStyle(.borderless)
                        }else{Text("No clear entrance was detected. The waveform and audio remain available.").foregroundStyle(.secondary)}
                    }
                    Spacer()
                    Button(audio.solos.contains(track.id) ? "Stop solo":"Listen solo"){audio.toggleSolo(track.id)}
                    Button {store.selectedTrack=nil} label:{Image(systemName:"xmark")}.buttonStyle(.plain).help("Close inspection")
                }
                HStack(spacing:16) {
                    ForEach(song.sections.filter{track.participation(from:$0.start,to:$0.end)>0.15}) {s in
                        Button(s.name){store.inspect(s)}.buttonStyle(.borderless)
                    }
                }.font(.system(size:12))
            }
        }
    }
    func energyDetail(_ song:StudySong,_ change:EnergyChange) -> some View {
        HStack(alignment:.top,spacing:30) {
            VStack(alignment:.leading,spacing:10) {
                Text(change.title).font(.system(size:23,weight:.semibold))
                Text("\(songTime(change.t)) · \(song.sections.first{change.t >= $0.start && change.t < $0.end}?.name ?? "Song")").font(.system(size:12)).foregroundStyle(.secondary)
                let words=change.contributors.compactMap(\.words).prefix(3)
                Text(words.isEmpty ? "A noticeable change in this recording's energy. Listen to what enters or drops back here.":words.joined(separator:". ")+".").font(.system(size:14)).fixedSize(horizontal:false,vertical:true)
                let entering=song.tracks.filter{!$0.quiet && $0.id != "other" && $0.participation(from:change.t,to:min(song.duration,change.t+4))-$0.participation(from:max(0,change.t-4),to:change.t)>0.4}.map(\.name)
                let leaving=song.tracks.filter{!$0.quiet && $0.id != "other" && $0.participation(from:max(0,change.t-4),to:change.t)-$0.participation(from:change.t,to:min(song.duration,change.t+4))>0.4}.map(\.name)
                if !entering.isEmpty {Text("Separated audio suggests an entrance from "+ListFormatter.localizedString(byJoining:entering)+".").font(.system(size:13)).foregroundStyle(.secondary)}
                if !leaving.isEmpty {Text("Separated audio suggests "+ListFormatter.localizedString(byJoining:leaving)+" dropping back.").font(.system(size:13)).foregroundStyle(.secondary)}
                Button("Listen to the change"){audio.seek(max(0,change.t-2),autoplay:true)}.buttonStyle(.borderless)
            }.frame(maxWidth:760,alignment:.leading)
            Spacer()
            Button {store.selectedEnergy=nil} label:{Image(systemName:"xmark")}.buttonStyle(.plain).foregroundStyle(.secondary).help("Close energy change")
        }
    }
    func sectionDetail(_ song:StudySong,_ section:SongSection) -> some View {
        HStack(alignment:.top,spacing:40) {
            VStack(alignment:.leading,spacing:10) {
                HStack(spacing:10){Text(section.name).font(.system(size:23,weight:.semibold));Button{store.renaming=section.id}label:{Image(systemName:"pencil")}.buttonStyle(.plain).foregroundStyle(.secondary).help("Rename section")}
                Text("\(songTime(section.start))–\(songTime(section.end))").font(.system(size:12)).foregroundStyle(.secondary)
                Text(explanation(song,section)).font(.system(size:14)).fixedSize(horizontal:false,vertical:true)
                HStack(spacing:18) {
                    Button("Listen from start"){store.inspect(section)}
                    Button("Compare with another section"){store.compare()}.disabled(song.sections.count<2)
                    Button("Evidence"){store.evidence.toggle()}.foregroundStyle(.secondary)
                }.buttonStyle(.borderless).font(.system(size:12)).padding(.top,5)
                if store.evidence {
                    Text("Energy is relative to this song. Separated parts are estimates, not original multitracks.\nSection timings are editable; the beat grid is estimated.").font(.system(size:11)).foregroundStyle(.secondary)
                    Text(String(format:"Mean energy %0.3f · curve %0.0f samples/s · audio 44,100 samples/s",song.energyMean(section),song.energyHz)).font(.system(size:11,design:.monospaced)).foregroundStyle(.secondary)
                    ForEach(Array(song.warnings.prefix(3).enumerated()),id:\.offset){_,warning in Text(warning).font(.system(size:11)).foregroundStyle(.secondary)}
                }
            }.frame(maxWidth:.infinity,alignment:.leading)
            VStack(alignment:.leading,spacing:9) {
                Text("In this section").font(.system(size:11)).foregroundStyle(.secondary)
                ForEach(song.tracks.filter{$0.participation(from:section.start,to:section.end)>0.15}) {track in
                    Button{store.inspectTrack(track)}label:{HStack(spacing:8){Circle().fill(Color(nsColor:NSColor(hex:track.color))).frame(width:5,height:5);Text(track.name)}}.buttonStyle(.plain).font(.system(size:13))
                }
            }.frame(width:140,alignment:.leading)
            Button {store.selected=nil;store.reference=nil}label:{Image(systemName:"xmark")}.buttonStyle(.plain).foregroundStyle(.secondary).help("Close inspection")
        }
    }
    func explanation(_ song:StudySong,_ section:SongSection) -> String {
        let active=song.tracks.filter{$0.participation(from:section.start,to:section.end)>0.45}.map(\.name)
        guard let previous=song.sections.last(where:{$0.start<section.start}) else {
            return active.isEmpty ? "Listen to the opening to hear how the song begins.":active.joined(separator:", ")+" carry the opening."
        }
        let added=song.tracks.filter{$0.participation(from:section.start,to:section.end)-$0.participation(from:previous.start,to:previous.end)>0.35}.map(\.name)
        let removed=song.tracks.filter{$0.participation(from:previous.start,to:previous.end)-$0.participation(from:section.start,to:section.end)>0.35}.map(\.name)
        if !added.isEmpty{return "More of this section includes "+added.joined(separator:" and ")+", compared with \(previous.name)."}
        if !removed.isEmpty{return removed.joined(separator:" and ")+" drop back compared with \(previous.name)."}
        let delta=song.energyMean(section)-song.energyMean(previous)
        if abs(delta)>0.025{return "The recording is \(delta>0 ? "more":"less") energetic here than in \(previous.name), with similar main parts."}
        return "The main parts continue from \(previous.name). Listen for how their roles change."
    }
    func comparison(_ song:StudySong,_ section:SongSection,_ reference:SongSection) -> some View {
        VStack(alignment:.leading,spacing:16) {
            HStack(alignment:.firstTextBaseline) {
                Text("\(section.name) compared with").font(.system(size:20,weight:.semibold))
                Picker("Reference section",selection:Binding(get:{store.reference ?? reference.id},set:{store.reference=$0})) {
                    ForEach(song.sections.filter{$0.id != section.id}){s in Text("\(s.name) · \(songTime(s.start))").tag(s.id)}
                }.labelsHidden().frame(maxWidth:260)
                Spacer()
                Button("Return to section"){store.reference=nil;audio.loop=nil}.buttonStyle(.borderless)
            }
            HStack(alignment:.top,spacing:28) {
                comparisonSide(song,reference)
                comparisonSide(song,section)
            }
            Text("In \(section.name) (\(songTime(section.start))), compared with \(reference.name) (\(songTime(reference.start))):").font(.system(size:13,weight:.medium))
            let delta=song.energyMean(section)-song.energyMean(reference)
            if abs(delta)>0.02 {
                Text("The recording feels \(delta>0 ? "more":"less") energetic.").font(.system(size:13))
            }else {Text("The overall energy is similar.").font(.system(size:13)).foregroundStyle(.secondary)}
            ForEach(song.tracks.filter{abs($0.participation(from:section.start,to:section.end)-$0.participation(from:reference.start,to:reference.end))>0.15}) {track in
                let more=track.participation(from:section.start,to:section.end)>track.participation(from:reference.start,to:reference.end)
                Text("\(track.name) plays through \(more ? "more":"less") of the passage.").font(.system(size:13))
            }
            Text("Original levels · A/B switches at the same elapsed position; if the target is shorter, it restarts at its beginning.").font(.system(size:11)).foregroundStyle(.secondary)
        }
    }
    func comparisonSide(_ song:StudySong,_ section:SongSection) -> some View {
        VStack(alignment:.leading,spacing:8) {
            HStack{Text(section.name).font(.system(size:14,weight:.semibold));Spacer();Text("\(songTime(section.start))–\(songTime(section.end))").font(.system(size:11)).foregroundStyle(.secondary)}
            ComparisonWaveform(song:song,section:section).frame(height:40+max(0,availableHeight-760)*0.06)
            HStack(spacing:8){Text("Energy").font(.system(size:10)).foregroundStyle(.secondary).frame(width:65,alignment:.leading);ComparisonEnergy(song:song,section:section,values:store.displayEnergy,scale:store.energyScale).frame(height:36+max(0,availableHeight-760)*0.06)}
            ForEach(store.visibleTracks) {track in
                HStack(spacing:8){Text(track.name).font(.system(size:11)).foregroundStyle(.secondary).frame(width:65,alignment:.leading);PartComparisonWaveform(track:track,section:section).frame(height:comparisonRowHeight)}
            }
            Button("Listen to \(section.name)"){store.switchComparison(to:section)}.buttonStyle(.borderless).font(.system(size:12))
        }.frame(maxWidth:.infinity)
    }
}
struct ComparisonWaveform:View {
    let song:StudySong
    let section:SongSection
    var body:some View {
        Canvas {context,size in
            guard !song.waveform.isEmpty else{return}
            var path=Path()
            for x in 0..<Int(size.width) {
                let t=section.start+Double(x)/size.width*(section.end-section.start)
                let index=min(song.waveform.count-1,max(0,Int(t*song.waveformHz)))
                let amplitude=min(1,Double(song.waveform[index])/max(0.001,song.waveformScale))*size.height*0.45
                path.move(to:CGPoint(x:Double(x),y:size.height/2-amplitude));path.addLine(to:CGPoint(x:Double(x),y:size.height/2+amplitude))
            }
            context.stroke(path,with:.color(.white.opacity(0.5)),lineWidth:1)
        }.accessibilityLabel("Waveform for \(section.name)")
    }
}

struct PartComparisonWaveform:View {
    let track:SongTrack
    let section:SongSection
    var body:some View {
        Canvas {context,size in
            guard !track.waveform.isEmpty else{return}
            var path=Path()
            for x in 0..<Int(size.width) {
                let t=section.start+Double(x)/size.width*(section.end-section.start)
                let a=min(track.waveform.count-1,max(0,Int(t*track.waveformHz)))
                let b=max(a+1,min(track.waveform.count,Int((t+(section.end-section.start)/size.width)*track.waveformHz)))
                var peak:Float=0
                for i in a..<b {peak=max(peak,track.waveform[i])}
                let amplitude=min(1,Double(peak)/max(0.001,track.waveformScale))*size.height*0.45
                path.move(to:CGPoint(x:Double(x),y:size.height/2-amplitude));path.addLine(to:CGPoint(x:Double(x),y:size.height/2+amplitude))
            }
            context.stroke(path,with:.color(Color(nsColor:NSColor(hex:track.color)).opacity(0.8)),lineWidth:1)
        }.accessibilityLabel("\(track.name) in \(section.name)")
    }
}
struct ComparisonEnergy:View {
    let song:StudySong
    let section:SongSection
    let values:[Double]
    let scale:EnergyScale
    var body:some View {
        Canvas {context,size in
            var path=Path()
            for x in 0..<Int(size.width) {
                let t=section.start+Double(x)/size.width*(section.end-section.start)
                let index=max(0,min(song.energy.count-1,Int(t*song.energyHz)))
                guard song.energy.indices.contains(index) else{continue}
                let point=CGPoint(x:Double(x),y:size.height*(1-scale.position(values[index])))
                if x==0{path.move(to:point)}else{path.addLine(to:point)}
            }
            context.stroke(path,with:.color(Color(red:0.75,green:0.68,blue:0.9)),lineWidth:1.5)
        }.accessibilityLabel("Energy in \(section.name), using the song's common scale")
    }
}
