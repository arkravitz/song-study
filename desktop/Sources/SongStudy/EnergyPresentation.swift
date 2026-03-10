import Foundation

struct EnergyContributor: Codable, Equatable {
    let key: String
    let delta: Double
    var words: String? {
        let up=delta>=0
        switch key {
        case "loudness": return up ? "The recording gets louder":"The recording gets quieter"
        case "onset_density": return up ? "Rhythmic attacks become more frequent":"Rhythmic attacks become less frequent"
        case "percussive_energy": return up ? "Transients get stronger":"Transients get softer"
        case "bass_activity": return up ? "The low end becomes more active":"The low end becomes less active"
        case "high_activity": return up ? "The high end becomes more active":"The high end becomes less active"
        case "band_spread": return up ? "More of the frequency range is active":"Less of the frequency range is active"
        case "hp_ratio": return up ? "Percussive sounds become more prominent":"Sustained sounds become more prominent"
        case "stereo_width": return up ? "The stereo image widens":"The stereo image narrows"
        default: return nil
        }
    }
}
struct EnergyChange: Codable, Equatable, Identifiable {
    let t: Double
    let direction: String
    let delta: Double
    let contributors: [EnergyContributor]
    var id: String {"\(t)-\(direction)"}
    var rises: Bool {direction=="rise"}
    var title: String {rises ? "Energy rises":"Energy drops"}
}

struct EnergyPresentation {
    // Two centered box passes form a triangular window. Centering avoids shifting entrances.
    static func smooth(_ values:[Double],hz:Double,seconds:Double) -> [Double] {
        guard hz>0,seconds>0,!values.isEmpty else{return values}
        let radius=min(values.count,max(0,Int((seconds*hz/4).rounded())))
        guard radius>0 else{return values}
        func pass(_ input:[Double]) -> [Double] {
            var prefix=[0.0];prefix.reserveCapacity(input.count+1)
            for value in input {prefix.append(prefix.last!+value)}
            return input.indices.map {i in
                let a=max(0,i-radius),b=min(input.count,i+radius+1)
                return (prefix[b]-prefix[a])/Double(b-a)
            }
        }
        return pass(pass(values))
    }
    static func largest(_ changes:[EnergyChange],rise:Bool,duration:Double,tail:Double?,bpm:Double) -> EnergyChange? {
        // A terminal fade is audible, but not an internal arrangement landmark.
        let ending=min(duration-max(6,bpm>0 ? 480/bpm:6),tail ?? duration)
        return changes.filter{$0.rises==rise && $0.t>=1 && $0.t<ending}
            .max{abs($0.delta)<abs($1.delta)}
    }
    static func visible(_ changes:[EnergyChange],window:TimelineWindow,width:Double) -> [EnergyChange] {
        let candidates=changes.filter{$0.t>=window.start && $0.t<=window.end}.sorted{abs($0.delta)>abs($1.delta)}
        var kept=[EnergyChange]()
        for c in candidates where !kept.contains(where:{$0.rises==c.rises && abs($0.t-c.t)/window.span*width<24}) {kept.append(c)}
        return kept.sorted{$0.t<$1.t}
    }
}
struct MusicalRuler {
    struct Bar: Equatable {let number:Int;let time:Double}
    static func position(at time:Double,beats:[Double],downbeats:[Double]) -> String? {
        guard let beat=beats.indices.min(by:{abs(beats[$0]-time)<abs(beats[$1]-time)}) else{return nil}
        guard !downbeats.isEmpty else{return "Beat \(beat+1)"}
        // Use detected downbeats rather than assuming four beats in every bar.
        let starts=downbeats.enumerated().compactMap {bar,t -> (bar:Int,beat:Int)? in
            guard let index=beats.indices.min(by:{abs(beats[$0]-t)<abs(beats[$1]-t)}) else{return nil}
            return (bar+1,index)
        }
        guard let start=starts.last(where:{$0.beat<=beat}) else{return "Pickup · beat \(beat+1)"}
        return "Bar \(start.bar).\(beat-start.beat+1)"
    }
    static func bars(downbeats:[Double],start:Double,end:Double,width:Double) -> [Bar] {
        guard end>start,width>0 else{return []}
        let visible=downbeats.enumerated().filter{$0.element>=start && $0.element<=end}
        let spacing=visible.count>1 ? width/Double(visible.count-1):width
        let step=[1,2,4,8,16,32,64].first{Double($0)*spacing>=58} ?? 128
        return visible.compactMap{i,t in i%step==0 ? Bar(number:i+1,time:t):nil}
    }
}
struct TimelineLayout {
    let height:Double
    let tracks:Int
    var sectionY:Double {44}
    var sectionHeight:Double {height>800 ? 52:44}
    var waveY:Double {sectionY+sectionHeight+28}
    var waveHeight:Double {min(100,54+max(0,height-620)*0.07)}
    var energyY:Double {waveY+waveHeight+24}
    var energyHeight:Double {min(170,86+max(0,height-620)*0.12)}
    var trackY:Double {energyY+energyHeight+32}
    var trackHeight:Double {max(44,(height-trackY-14)/Double(max(1,tracks)))}
    static func height(available:Double,tracks:Int,inspecting:Bool,zoomed:Bool) -> Double {
        let reserved=190.0+(zoomed ? 42:0)
        return max(320+Double(tracks)*44,available-reserved)
    }
}
