import Foundation

let songSampleRate = 44100.0

struct WaveformAsset: Codable {
    let path:String
    let values:[Float]
    var count:Int {values.count}
    var isEmpty:Bool {values.isEmpty}
    subscript(_ index:Int)->Float {values[index]}
    init(from decoder:Decoder) throws {
        path=try decoder.singleValueContainer().decode(String.self)
        let data=try Data(contentsOf:URL(fileURLWithPath:path),options:.mappedIfSafe)
        guard data.count % 4 == 0 else {throw WorkerFailure(message:"The waveform cache is incomplete.")}
        values=data.withUnsafeBytes {buffer in
            stride(from:0,to:buffer.count,by:4).map{buffer.loadUnaligned(fromByteOffset:$0,as:Float.self)}
        }
    }
    func encode(to encoder:Encoder) throws {var container=encoder.singleValueContainer();try container.encode(path)}
}
struct LibrarySong: Codable, Identifiable {
    let id: String
    let title: String
    let artist: String
    let duration: Double
}
struct ActivitySpan: Codable { let start: Double; let end: Double }
struct SongTrack: Codable, Identifiable {
    let id: String
    let name: String
    let color: String
    let quiet: Bool
    let audioPath: String
    let waveform: WaveformAsset
    let waveformHz: Double
    let waveformScale: Double
    let segments: [ActivitySpan]
    func participation(from: Double, to: Double) -> Double {
        guard to > from else { return 0 }
        return min(1, segments.reduce(0) { $0 + max(0,min($1.end,to)-max($1.start,from)) } / (to-from))
    }
}
enum SectionRole: CaseIterable {
    case intro, verse, preChorus, chorus, postChorus, bridge, instrumental, outro, unknown
    private var pattern:String {
        switch self {
        case .intro:return "intro(?:duction)?"
        case .verse:return "verse"
        case .preChorus:return "pre[\\s_–—-]*chorus"
        case .chorus:return "(?:chorus|refrain|hook)"
        case .postChorus:return "post[\\s_–—-]*chorus"
        case .bridge:return "bridge"
        case .instrumental:return "(?:instrumental|solo|interlude|breakdown)"
        case .outro:return "(?:outro|ending|coda)"
        case .unknown:return "(?!)"
        }
    }
    private static let matchers:[(SectionRole,NSRegularExpression)] = allCases.compactMap {role in
        guard let regex=try? NSRegularExpression(pattern:"^\\s*(?:(?:first|second|third|final|last)\\s+)?"+role.pattern+"(?=\\b|\\d)",options:.caseInsensitive) else{return nil}
        return (role,regex)
    }
    static func named(_ name:String) -> SectionRole {
        let range=NSRange(name.startIndex..<name.endIndex,in:name)
        return matchers.first{$0.1.firstMatch(in:name,range:range) != nil}?.0 ?? .unknown
    }
    var fill:String {
        switch self {
        case .intro:return "30313b"
        case .verse:return "27374b"
        case .preChorus:return "453b29"
        case .chorus:return "263e34"
        case .postChorus:return "294044"
        case .bridge:return "3d304b"
        case .instrumental:return "3f3438"
        case .outro:return "35323e"
        case .unknown:return "252d39"
        }
    }
    var ink:String {
        switch self {
        case .intro:return "d3d3df"
        case .verse:return "bed5f1"
        case .preChorus:return "e9cf9b"
        case .chorus:return "b8dfc6"
        case .postChorus:return "b5dce1"
        case .bridge:return "d7bfe9"
        case .instrumental:return "e3c5ce"
        case .outro:return "cdc5df"
        case .unknown:return "e1e6ee"
        }
    }
}
struct SongSection: Codable, Identifiable, Equatable {
    var id: String
    var name: String
    var kind: String
    var repeatGroup: Int?
    var startFrame: Int64
    var endFrame: Int64
    var start: Double { Double(startFrame)/songSampleRate }
    var end: Double { Double(endFrame)/songSampleRate }
    var displayRole:SectionRole {SectionRole.named(name)}
    var comparisonName: String {
        name.lowercased().trimmingCharacters(in:.whitespacesAndNewlines)
            .replacingOccurrences(of:"\\s+\\d+$",with:"",options:.regularExpression)
    }
}
struct StudySong: Codable, Identifiable {
    let id: String
    let title: String
    let artist: String
    let duration: Double
    let sampleRate: Int
    let audioPath: String
    let waveform: WaveformAsset
    let waveformHz: Double
    let waveformScale: Double
    var sections: [SongSection]
    let tracks: [SongTrack]
    let energy: [Double]
    let energyHz: Double
    let bpm: Double
    let beats: [Double]
    let downbeats: [Double]
    let energyChanges: [EnergyChange]
    let warnings: [String]
    var canUndo: Bool
    var canRedo: Bool
    func energyMean(_ section: SongSection) -> Double {
        let a = min(energy.count,max(0,Int(section.start*energyHz)))
        let b = min(energy.count,max(a,Int(section.end*energyHz)))
        guard b > a else { return 0 }
        return energy[a..<b].reduce(0,+)/Double(b-a)
    }
}
struct EditResult: Codable { let sections:[SongSection]; let canUndo:Bool; let canRedo:Bool }

struct SectionEditing {
    static func split(_ sections: [SongSection], at frame: Int64) -> [SongSection] {
        guard let i=sections.firstIndex(where: {frame >= $0.startFrame+4410 && frame <= $0.endFrame-4410}) else {return sections}
        var result=sections
        var right=result[i]; right.id=UUID().uuidString; right.startFrame=frame
        result[i].endFrame=frame
        result.insert(right,at:i+1)
        return result
    }
    static func merge(_ sections:[SongSection],boundary:Int) -> [SongSection] {
        guard boundary>0,boundary<sections.count else{return sections}
        var result=sections
        result[boundary-1].endFrame=result[boundary].endFrame
        result.remove(at:boundary)
        return result
    }
    static func move(_ sections:[SongSection],boundary:Int,to frame:Int64,snap:Bool,beats:[Double]) -> [SongSection] {
        guard boundary>0,boundary<sections.count else{return sections}
        var frame=frame
        if snap,let nearest=beats.min(by:{abs($0-Double(frame)/songSampleRate)<abs($1-Double(frame)/songSampleRate)}) {
            frame=Int64((nearest*songSampleRate).rounded())
        }
        frame=max(sections[boundary-1].startFrame+4410,min(sections[boundary].endFrame-4410,frame))
        var result=sections; result[boundary-1].endFrame=frame; result[boundary].startFrame=frame
        return result
    }
}
struct TimelineWindow: Equatable {
    var start:Double
    var end:Double
    var duration:Double
    var span:Double {max(0.1,end-start)}
    func zoom(factor:Double,anchor:Double) -> TimelineWindow {
        let point=start+span*anchor
        let width=min(duration,max(0.5,span/factor))
        let a=max(0,min(duration-width,point-width*anchor))
        return TimelineWindow(start:a,end:a+width,duration:duration)
    }
    func pan(seconds:Double) -> TimelineWindow {
        let a=max(0,min(duration-span,start+seconds))
        return TimelineWindow(start:a,end:a+span,duration:duration)
    }
}
struct EnergyScale {
    let low:Double
    let high:Double
    let tailStart:Double?
    init(values:[Double],frameHz:Double) {
        guard values.count>3,frameHz>0 else {low=0;high=1;tailStart=nil;return}
        let sorted=values.sorted()
        let median=sorted[sorted.count/2]
        let spread=sorted[Int(Double(sorted.count-1)*0.95)]-sorted[Int(Double(sorted.count-1)*0.05)]
        let quietThreshold=median-max(0.025,spread*0.6)
        var tail=values.count
        if values.last! < quietThreshold {
            while tail>max(0,values.count-Int(min(20,Double(values.count)/frameHz*0.15)*frameHz)),values[tail-1]<quietThreshold {tail-=1}
        }
        let body=Array(values.prefix(max(1,tail))).sorted()
        low=body[Int(Double(body.count-1)*0.05)]
        high=max(low+0.015,body[Int(Double(body.count-1)*0.95)])
        tailStart=tail<values.count ? Double(tail)/frameHz : nil
    }
    func position(_ value:Double) -> Double {
        if value<low {return 0.12 * max(0,value)/max(0.001,low)}
        return 0.16 + 0.80 * min(1,max(0,(value-low)/(high-low)))
    }
    func internalDrop(values:[Double],frameHz:Double) -> Double? {
        guard frameHz>0,values.count>4 else{return nil}
        let step=max(1,Int(frameHz*1.5))
        let until=min(values.count-step,Int((tailStart ?? Double(values.count)/frameHz-1.5)*frameHz)-step)
        guard until>step else{return nil}
        var best=0.0; var position:Double?
        for i in stride(from:step,to:until,by:step) {
            let before=values[(i-step)..<i].reduce(0,+)/Double(step)
            let after=values[i..<(i+step)].reduce(0,+)/Double(step)
            if before-after>best {best=before-after;position=Double(i)/frameHz}
        }
        return best>0.025 ? position : nil
    }
}
struct AudioRouting {
    static func gain(for id:String,solos:Set<String>) -> Float {
        if id=="mix" {return solos.isEmpty ? 1:0}
        return solos.contains(id) ? 1:0
    }
}
func songTime(_ seconds:Double) -> String {
    let value=max(0,Int(seconds));return String(format:"%d:%02d",value/60,value%60)
}
