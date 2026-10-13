import AVFoundation
import Combine
import Foundation

@MainActor final class AudioTransport: ObservableObject {
    @Published private(set) var position=0.0
    @Published private(set) var playing=false
    @Published private(set) var solos=Set<String>()
    @Published var volume:Double=1 {
        didSet {
            let bounded=volume.isFinite ? max(0,min(1,volume)):0
            if volume != bounded {volume=bounded;return}
            engine.mainMixerNode.outputVolume=Float(volume)
        }
    }
    @Published var loop:ClosedRange<Double>?
    private let engine=AVAudioEngine()
    private var nodes:[String:AVAudioPlayerNode]=[:]
    private var files:[String:AVAudioFile]=[:]
    private var timer:Timer?
    private var seekOrigin=0.0
    private var scheduledHost:UInt64=0
    private var duration=0.0
    private var smoothing:[String:Float]=[:]
    private var scrubResume:Bool?
    private var pendingScrubPosition:Double?
    private var lastScrubSchedule=0.0
    init() {
        timer=Timer.scheduledTimer(withTimeInterval:1/60,repeats:true) { [weak self] _ in
            Task { @MainActor [weak self] in self?.tick() }
        }
    }
    func load(_ song:StudySong) throws {
        scrubResume=nil;pendingScrubPosition=nil
        pause();engine.stop()
        for node in nodes.values {engine.detach(node)}
        nodes.removeAll();files.removeAll();smoothing.removeAll();solos=[];loop=nil
        duration=song.duration;position=0;seekOrigin=0
        let paths=[("mix",song.audioPath)]+song.tracks.map{($0.id,$0.audioPath)}
        for (id,path) in paths {files[id]=try AVAudioFile(forReading:URL(fileURLWithPath:path))}
        guard let format=files["mix"]?.processingFormat else{return}
        let identifiers=Set(files.keys).union(["vocals","drums","bass","guitar","piano","other"])
        for id in identifiers {
            let node=AVAudioPlayerNode();engine.attach(node)
            engine.connect(node,to:engine.mainMixerNode,format:format)
            nodes[id]=node
            node.volume=id=="mix" ? 1:0;smoothing[id]=node.volume
        }
        engine.mainMixerNode.outputVolume=Float(volume)
        engine.prepare();try engine.start()
    }
    func addPreparedTracks(_ song:StudySong) throws {
        for track in song.tracks where files[track.id]==nil {
            let file=try AVAudioFile(forReading:URL(fileURLWithPath:track.audioPath))
            files[track.id]=file
            guard let node=nodes[track.id] else{continue}
            if playing {
                let host=mach_absolute_time()+AVAudioTime.hostTime(forSeconds:0.08)
                let songTime=seekOrigin+AVAudioTime.seconds(forHostTime:host-scheduledHost)
                let frame=AVAudioFramePosition((songTime*file.processingFormat.sampleRate).rounded())
                if frame<file.length {
                    node.scheduleSegment(file,startingFrame:frame,frameCount:AVAudioFrameCount(min(file.length-frame,Int64(UInt32.max))),at:nil)
                    node.play(at:AVAudioTime(hostTime:host))
                }
            }
        }
    }
    func toggle() {if playing {pause()}else{play()}}
    func play() {
        guard !files.isEmpty,!playing else{return}
        if position>=duration-0.01 {position=loop?.lowerBound ?? 0}
        if !engine.isRunning {do{try engine.start()}catch{return}}
        seekOrigin=position
        // Every player starts on the same host tick, after scheduling all disk segments.
        scheduledHost=mach_absolute_time()+AVAudioTime.hostTime(forSeconds:scrubResume == nil ? 0.06:0.008)
        let time=AVAudioTime(hostTime:scheduledHost)
        for (id,node) in nodes {
            guard let file=files[id] else{continue}
            node.stop()
            let start=AVAudioFramePosition((position*file.processingFormat.sampleRate).rounded())
            let count=file.length-start
            guard count>0 else{continue}
            node.scheduleSegment(file,startingFrame:start,frameCount:AVAudioFrameCount(min(count,Int64(UInt32.max))),at:nil)
            node.play(at:time)
        }
        playing=true
    }
    func pause() {
        updatePosition()
        playing=false
        for node in nodes.values {node.stop()}
        seekOrigin=position
    }
    func seek(_ seconds:Double,autoplay:Bool?=nil) {
        let resume=autoplay ?? playing
        pause();position=max(0,min(duration,seconds));seekOrigin=position
        if resume {play()}
    }
    func beginScrubbing(at seconds:Double) {
        scrubResume=playing
        pendingScrubPosition=nil
        seek(seconds,autoplay:scrubResume)
        lastScrubSchedule=ProcessInfo.processInfo.systemUptime
    }
    func scrub(to seconds:Double) {
        guard scrubResume != nil else{return}
        position=max(0,min(duration,seconds))
        pendingScrubPosition=position
        flushScrub()
    }
    private func flushScrub() {
        guard let resume=scrubResume,let target=pendingScrubPosition else{return}
        // Coalesce mouse events so audible snippets can render between seeks.
        guard !resume || ProcessInfo.processInfo.systemUptime-lastScrubSchedule>=0.08 else{return}
        pendingScrubPosition=nil
        seek(target,autoplay:resume)
        lastScrubSchedule=ProcessInfo.processInfo.systemUptime
    }
    func endScrubbing(at seconds:Double) {
        guard let resume=scrubResume else{return}
        // Keep the drag target authoritative until the final seek is scheduled.
        seek(seconds,autoplay:resume)
        scrubResume=nil;pendingScrubPosition=nil
    }
    func toggleSolo(_ id:String) {
        if solos.contains(id){solos.remove(id)}else{solos.insert(id)}
        // Gain changes do not call stop, scheduleSegment, or seek.
    }
    func clearSolos(){solos=[]}
    func shutdown(){pause();engine.stop()}
    private func updatePosition() {
        guard scrubResume == nil,playing,let node=nodes["mix"],let render=node.lastRenderTime,
              render.isHostTimeValid,render.hostTime>=scheduledHost,
              let player=node.playerTime(forNodeTime:render),player.isSampleTimeValid else{return}
        let latency=engine.outputNode.presentationLatency
        position=min(duration,max(seekOrigin,seekOrigin+Double(player.sampleTime)/player.sampleRate-latency))
    }
    private func tick() {
        for (id,node) in nodes {
            let target=AudioRouting.gain(for:id,solos:solos)
            let current=smoothing[id] ?? target
            let gain=abs(target-current)<0.005 ? target:current+(target-current)*0.45
            node.volume=gain;smoothing[id]=gain
        }
        if scrubResume != nil {flushScrub();return}
        updatePosition()
        if playing,let loop,position>=loop.upperBound {seek(loop.lowerBound,autoplay:true)}
        else if playing,position>=duration-0.04 {pause();position=duration}
    }
}
