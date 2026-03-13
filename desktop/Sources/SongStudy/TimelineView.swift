import AppKit
import SwiftUI

extension NSColor {
    convenience init(hex:String) {
        let clean=hex.trimmingCharacters(in:CharacterSet.alphanumerics.inverted)
        let value=UInt64(clean,radix:16) ?? 0x85929e
        self.init(red:CGFloat((value>>16)&255)/255,green:CGFloat((value>>8)&255)/255,blue:CGFloat(value&255)/255,alpha:1)
    }
}
struct TimelineHost:NSViewRepresentable {
    @ObservedObject var store:StudyStore
    @ObservedObject var audio:AudioTransport
    func makeNSView(context:Context) -> SongTimelineView {let view=SongTimelineView();view.store=store;return view}
    func updateNSView(_ view:SongTimelineView,context:Context){view.store=store;view.needsDisplay=true;view.updateAccessibility()}
}

final class SectionAccessibility:NSAccessibilityElement {
    weak var timeline:SongTimelineView?
    var sectionID=""
    var trackID:String?
    var solo=false
    var change:EnergyChange?
    var energySettings=false
    override func accessibilityPerformPress() -> Bool {
        MainActor.assumeIsolated {
            guard let view=timeline else{return false}
            if energySettings {view.store?.energySettingsOpen=true;return true}
            if let change {view.store?.inspectEnergy(change);return true}
            if let id=trackID {
                if solo {view.store?.audio.toggleSolo(id)}
                else if let track=view.store?.song?.tracks.first(where:{$0.id==id}) {view.store?.inspectTrack(track)}
                return true
            }
            guard let section=view.store?.song?.sections.first(where:{$0.id==sectionID}) else{return false}
            view.store?.inspect(section);return true
        }
    }
}

@MainActor final class SongTimelineView:NSView {
    weak var store:StudyStore?
    var dragBoundary:Int?
    var preview:[SongSection]?
    var downPoint:NSPoint?
    var focusedBoundary:Int?
    var scrubbing=false
    private var tracking:NSTrackingArea?
    private var accessible:[String:SectionAccessibility]=[:]
    private var accessSignature=""
    let gutter:CGFloat=136
    var layout:TimelineLayout {TimelineLayout(height:Double(bounds.height),tracks:store?.visibleTracks.count ?? 0)}
    var sectionY:CGFloat {layout.sectionY}
    var sectionHeight:CGFloat {layout.sectionHeight}
    var waveY:CGFloat {layout.waveY}
    var waveHeight:CGFloat {layout.waveHeight}
    var energyY:CGFloat {layout.energyY}
    var energyHeight:CGFloat {layout.energyHeight}
    var trackY:CGFloat {layout.trackY}
    var trackHeight:CGFloat {layout.trackHeight}
    override var isFlipped:Bool {true}
    override var acceptsFirstResponder:Bool {true}
    override func setFrameSize(_ newSize:NSSize) {
        super.setFrameSize(newSize)
        updateAccessibility()
        needsDisplay=true
    }
    var plotWidth:CGFloat {max(1,bounds.width-gutter-18)}
    func x(_ time:Double) -> CGFloat {
        guard let window=store?.window else{return gutter}
        return gutter+CGFloat((time-window.start)/window.span)*plotWidth
    }
    func time(_ x:CGFloat) -> Double {
        guard let window=store?.window else{return 0}
        return max(0,min(window.duration,window.start+Double((x-gutter)/plotWidth)*window.span))
    }
    func text(_ string:String,x:CGFloat,y:CGFloat,size:CGFloat=12,color:NSColor = .white,weight:NSFont.Weight = .regular,width:CGFloat?=nil) {
        let style=NSMutableParagraphStyle();style.lineBreakMode = .byTruncatingTail
        let attrs:[NSAttributedString.Key:Any]=[.font:NSFont.systemFont(ofSize:size,weight:weight),.foregroundColor:color,.paragraphStyle:style]
        (string as NSString).draw(in:NSRect(x:x,y:y,width:width ?? bounds.width-x,height:size+7),withAttributes:attrs)
    }
    override func draw(_ dirtyRect:NSRect) {
        guard let store,let song=store.song,let context=NSGraphicsContext.current?.cgContext else{return}
        NSColor(hex:"14171c").setFill();bounds.fill()
        let sections=preview ?? song.sections
        let window=store.window
        let muted=NSColor(hex:"9ba4b1")
        text("SECTIONS",x:14,y:sectionY+15,size:10,color:muted,weight:.medium,width:98)
        text("Song",x:14,y:waveY+19,color:muted)
        text("Energy  ▾",x:14,y:energyY+20,color:NSColor(hex:"baaae9"))
        text(store.energyWindow<0.04 ? "Raw":String(format:"%.1f s smooth",store.energyWindow),x:14,y:energyY+40,size:10,color:muted,width:110)
        // The body scale stays fixed across zoom and comparison. Below it is an explicitly marked quiet band.
        let energyScale=store.energyScale
        let energy=store.displayEnergy
        text(song.downbeats.isEmpty ? "Time":"Bars",x:14,y:3,size:10,color:muted,width:100)
        text(song.downbeats.isEmpty ? "":"Time",x:14,y:22,size:9,color:muted,width:100)
        context.saveGState()
        context.clip(to:NSRect(x:gutter,y:0,width:plotWidth,height:bounds.height))
        // Separate ruler tiers keep musical position primary and elapsed time available.
        let bars=MusicalRuler.bars(downbeats:song.downbeats,start:window.start,end:window.end,width:Double(plotWidth))
        for bar in bars {
            text("\(bar.number)",x:x(bar.time)+3,y:2,size:11,color:NSColor(hex:"d6dce5"),weight:.medium,width:48)
            stroke(from:NSPoint(x:x(bar.time),y:40),to:NSPoint(x:x(bar.time),y:bounds.height-12),color:NSColor.white.withAlphaComponent(0.07))
        }
        let step=max(1,pow(10,floor(log10(max(1,window.span/8)))))
        let timeStep=window.span/step>18 ? step*5:step
        for t in stride(from:floor(window.start/timeStep)*timeStep,through:window.end,by:timeStep) where t>=window.start {
            text(songTime(t),x:x(t)+3,y:song.downbeats.isEmpty ? 4:23,size:9,color:muted,width:65)
        }
        if window.span<20 {
            for beat in song.beats where beat>=window.start && beat<=window.end {
                stroke(from:NSPoint(x:x(beat),y:sectionY+sectionHeight),to:NSPoint(x:x(beat),y:bounds.height-12),color:NSColor.white.withAlphaComponent(0.035))
            }
        }
        var narrowLabelEnd:CGFloat=gutter
        for (index,section) in sections.enumerated() where section.end>=window.start && section.start<=window.end {
            let left=max(gutter,x(section.start)),right=min(gutter+plotWidth,x(section.end))
            let width=right-left
            let role=section.displayRole
            let fill=NSColor(hex:role.fill)
            fill.setFill();NSRect(x:left,y:sectionY,width:max(1,width),height:sectionHeight).fill()
            let selected=store.selected==section.id
            let border=selected ? NSColor(hex:"d5e1d7"):NSColor(hex:"49535e")
            border.setStroke();NSBezierPath(rect:NSRect(x:left+0.5,y:sectionY+0.5,width:max(0,width-1),height:sectionHeight-1)).stroke()
            let labelColor=NSColor(hex:role.ink)
            let measured=(section.name as NSString).size(withAttributes:[.font:NSFont.systemFont(ofSize:12,weight:.medium)]).width
            if store.renaming==section.id { /* Native field overlays this label. */ } else if width>=measured+20 {
                text(section.name,x:left+10,y:sectionY+13,size:bounds.width>1600 ? 14:12,color:labelColor,weight:.medium,width:width-15)
            } else {
                let labelLeft=max(left,narrowLabelEnd)
                let labelWidth=min(150,max(45,measured+5))
                if labelLeft+labelWidth<=gutter+plotWidth {
                    text(section.name,x:labelLeft,y:sectionY+sectionHeight+6,size:10,color:labelColor,width:labelWidth)
                    stroke(from:NSPoint(x:(left+right)/2,y:sectionY+sectionHeight),to:NSPoint(x:labelLeft+4,y:sectionY+sectionHeight+5),color:border)
                    narrowLabelEnd=labelLeft+labelWidth+8
                } else {text("\(index+1)",x:left+2,y:sectionY+13,size:10,color:labelColor,width:max(10,width))}
            }
        }
        waveform(song.waveform,rate:song.waveformHz,scale:song.waveformScale,y:waveY,height:waveHeight,color:NSColor(hex:"7c899b"))
        let quietY=energyY+energyHeight*(1-0.16)
        NSColor(hex:"222029").setFill();NSRect(x:gutter,y:quietY,width:plotWidth,height:energyHeight*0.16).fill()
        stroke(from:NSPoint(x:gutter,y:quietY),to:NSPoint(x:gutter+plotWidth,y:quietY),color:NSColor(hex:"665a79"),dashed:true)
        if !song.energy.isEmpty {
            let path=NSBezierPath();let a=max(0,Int(window.start*song.energyHz)),b=min(song.energy.count,Int(window.end*song.energyHz)+1)
            path.move(to:NSPoint(x:x(Double(a)/song.energyHz),y:energyY+energyHeight))
            for i in a..<b {path.line(to:NSPoint(x:x(Double(i)/song.energyHz),y:energyY+energyHeight*(1-energyScale.position(energy[i]))))}
            path.line(to:NSPoint(x:x(Double(max(a,b-1))/song.energyHz),y:energyY+energyHeight));path.close()
            NSColor(hex:"a38ccc").withAlphaComponent(0.25).setFill();path.fill()
            let line=NSBezierPath()
            for i in a..<b {
                let point=NSPoint(x:x(Double(i)/song.energyHz),y:energyY+energyHeight*(1-energyScale.position(energy[i])))
                if i==a {line.move(to:point)} else {line.line(to:point)}
            }
            NSColor(hex:"c1afea").setStroke();line.lineWidth=1.7;line.stroke()
        }
        drawEnergyMarkers()
        if let tail=energyScale.tailStart,tail>=window.start,tail<window.end {
            text("Quiet ending",x:max(gutter+5,min(x(tail)-80,gutter+plotWidth-90)),y:quietY+1,size:9,color:muted,width:88)
        } else {text("quiet range",x:gutter+5,y:quietY+1,size:9,color:muted,width:75)}
        for (i,track) in store.visibleTracks.enumerated() {
            let y=trackY+CGFloat(i)*trackHeight
            if let selected=store.selectedTrack,selected==track.id {NSColor(hex:"222a34").setFill();NSRect(x:gutter,y:y,width:plotWidth,height:trackHeight-4).fill()}
            for span in track.segments where span.end>=window.start && span.start<=window.end {
                NSColor(hex:track.color).withAlphaComponent(0.07).setFill()
                NSRect(x:x(span.start),y:y+5,width:max(0,x(span.end)-x(span.start)),height:trackHeight-14).fill()
            }
            let dimmed = !store.audio.solos.isEmpty && !store.audio.solos.contains(track.id)
            waveform(track.waveform,rate:track.waveformHz,scale:track.waveformScale,y:y+8,height:trackHeight-22,color:NSColor(hex:track.color).withAlphaComponent(dimmed ? 0.25:0.8))
            stroke(from:NSPoint(x:gutter,y:y+trackHeight-5),to:NSPoint(x:gutter+plotWidth,y:y+trackHeight-5),color:NSColor.white.withAlphaComponent(0.07))
        }
        if let loop=store.audio.loop {
            NSColor(hex:"c4d5bd").withAlphaComponent(0.07).setFill()
            NSRect(x:x(loop.lowerBound),y:sectionY,width:x(loop.upperBound)-x(loop.lowerBound),height:bounds.height-sectionY).fill()
        }
        let play=x(store.audio.position)
        if play>=gutter,play<=gutter+plotWidth {
            stroke(from:NSPoint(x:play,y:sectionY-6),to:NSPoint(x:play,y:bounds.height-8),color:NSColor(hex:"f3eee5"),width:1.3)
            let marker=NSBezierPath();marker.move(to:NSPoint(x:play-4,y:sectionY-6));marker.line(to:NSPoint(x:play+4,y:sectionY-6));marker.line(to:NSPoint(x:play,y:sectionY+1));marker.close();NSColor(hex:"f3eee5").setFill();marker.fill()
        }
        if let index=dragBoundary,let preview {
            let t=preview[index].start
            let tenths=Int((max(0,t)*10).rounded())
            let timestamp=String(format:"%d:%02d.%d",tenths/600,(tenths/10)%60,tenths%10)
            let position=MusicalRuler.position(at:t,beats:song.beats,downbeats:song.downbeats)
            let snap=store.snap && !NSEvent.modifierFlags.contains(.shift)
            let label=[timestamp,position,snap ? "Snap":"Free"].compactMap{$0}.joined(separator:" · ")
            let labelWidth=(label as NSString).size(withAttributes:[.font:NSFont.systemFont(ofSize:12,weight:.medium)]).width+20
            let labelX=max(gutter+3,min(x(t)+7,gutter+plotWidth-labelWidth-3))
            let labelY=sectionY+sectionHeight+3
            let box=NSRect(x:labelX,y:labelY,width:labelWidth,height:23)
            NSColor(hex:"10151d").setFill();NSBezierPath(roundedRect:box,xRadius:4,yRadius:4).fill()
            text(label,x:labelX+10,y:labelY+4,size:12,color:.white,weight:.medium,width:labelWidth-20)
        }
        context.restoreGState()
        for (i,track) in store.visibleTracks.enumerated() {
            let y=trackY+CGFloat(i)*trackHeight
            NSColor(hex:track.color).setFill();NSRect(x:14,y:y+23,width:4,height:4).fill()
            text(track.name,x:25,y:y+16,size:bounds.width>1600 ? 13:12,color:muted,width:80)
            let solo=store.audio.solos.contains(track.id)
            (solo ? NSColor(hex:"d2dacc"):NSColor(hex:"252a31")).setFill()
            NSBezierPath(roundedRect:NSRect(x:108,y:y+12,width:24,height:25),xRadius:4,yRadius:4).fill()
            text("S",x:116,y:y+17,size:11,color:solo ? NSColor.black:muted,width:14)
        }
        stroke(from:NSPoint(x:gutter,y:0),to:NSPoint(x:gutter,y:bounds.height),color:NSColor(hex:"313741"))
        // SwiftUI can apply the final bounds after updateNSView and frame sizing.
        // Refresh once those bounds are drawn, including while playback is paused.
        updateAccessibility()
    }
    var visibleEnergyChanges:[EnergyChange] {
        guard let store,let song=store.song,store.showEnergyChanges else{return []}
        return EnergyPresentation.visible(song.energyChanges,window:store.window,width:Double(plotWidth))
    }
    func hitEnergy(_ p:NSPoint) -> EnergyChange? {
        guard p.x>=gutter,p.x<=gutter+plotWidth else{return nil}
        return visibleEnergyChanges.first {c in
            let y=c.rises ? energyY+energyHeight-10:energyY+10
            return abs(x(c.t)-p.x)<10 && abs(y-p.y)<14
        }
    }
    func drawEnergyMarkers() {
        guard let store else{return}
        for c in visibleEnergyChanges {
            let biggest=store.largestEnergy(rise:c.rises)?.id==c.id
            let color=NSColor(hex:c.rises ? "e9b96d":"83b8e8")
            let xx=x(c.t),base=c.rises ? energyY+energyHeight-1:energyY+1
            let size:CGFloat=biggest ? 7:5
            let tip=base+(c.rises ? -size*2:size*2)
            stroke(from:NSPoint(x:xx,y:energyY+15),to:NSPoint(x:xx,y:energyY+energyHeight-15),color:color.withAlphaComponent(store.selectedEnergy?.id==c.id ? 0.7:0.2))
            let path=NSBezierPath();path.move(to:NSPoint(x:xx-size,y:base));path.line(to:NSPoint(x:xx+size,y:base));path.line(to:NSPoint(x:xx,y:tip));path.close();color.setFill();path.fill()
            if biggest {text(c.rises ? "Biggest rise":"Biggest drop",x:max(gutter+3,min(xx+10,gutter+plotWidth-90)),y:c.rises ? base-17:base+2,size:9,color:color,width:84)}
        }
    }
    func stroke(from:NSPoint,to:NSPoint,color:NSColor,width:CGFloat=1,dashed:Bool=false) {
        let path=NSBezierPath();path.move(to:from);path.line(to:to);path.lineWidth=width
        if dashed {let d:[CGFloat]=[3,4];path.setLineDash(d,count:2,phase:0)}
        color.setStroke();path.stroke()
    }
    func waveform(_ values:WaveformAsset,rate:Double,scale:Double,y:CGFloat,height:CGFloat,color:NSColor) {
        guard let window=store?.window,!values.isEmpty,rate>0 else{return}
        let middle=y+height/2
        let path=NSBezierPath()
        for pixel in 0..<Int(plotWidth) {
            let t=window.start+Double(pixel)/Double(plotWidth)*window.span
            let a=max(0,min(values.count-1,Int(t*rate)))
            let b=max(a+1,min(values.count,Int((t+window.span/Double(plotWidth))*rate)))
            var peak:Float=0
            for i in a..<b {peak=max(peak,values[i])}
            let amplitude=min(1,Double(peak)/max(0.0001,scale))*Double(height/2-2)
            path.move(to:NSPoint(x:gutter+CGFloat(pixel),y:middle-CGFloat(amplitude)))
            path.line(to:NSPoint(x:gutter+CGFloat(pixel),y:middle+CGFloat(amplitude)))
        }
        color.setStroke();path.lineWidth=1;path.stroke()
    }
    override func updateTrackingAreas() {
        super.updateTrackingAreas();if let tracking{removeTrackingArea(tracking)}
        tracking=NSTrackingArea(rect:bounds,options:[.mouseMoved,.activeInKeyWindow,.inVisibleRect],owner:self,userInfo:nil)
        if let tracking {addTrackingArea(tracking)}
    }
    override func mouseMoved(with event:NSEvent) {
        let p=convert(event.locationInWindow,from:nil)
        if p.y>=sectionY,p.y<=sectionY+sectionHeight,let sections=store?.song?.sections,sections.dropFirst().contains(where:{abs(x($0.start)-p.x)<6}) {NSCursor.resizeLeftRight.set()}
        else {NSCursor.arrow.set()}
        if let c=hitEnergy(p) {toolTip="\(c.title) · \(songTime(c.t))\nClick to listen and see what changed.";NSCursor.pointingHand.set();return}
        if p.x<gutter,p.y>=energyY,p.y<=energyY+energyHeight {toolTip="Adjust energy smoothing and change markers";NSCursor.pointingHand.set();return}
        if let section=store?.song?.sections.first(where:{time(p.x)>=$0.start && time(p.x)<$0.end}),p.y<sectionY+sectionHeight {toolTip="\(section.name) · \(songTime(section.start))–\(songTime(section.end))\nClick to listen. Right-click to rename, loop or compare."}else{toolTip=nil}
    }
    override func mouseDown(with event:NSEvent) {
        guard let store,let song=store.song else{return}
        window?.makeFirstResponder(self)
        let p=convert(event.locationInWindow,from:nil);downPoint=p
        if p.x<gutter,p.y>=energyY,p.y<=energyY+energyHeight {store.energySettingsOpen=true;return}
        if let change=hitEnergy(p) {store.inspectEnergy(change);return}
        if p.y>=sectionY,p.y<=sectionY+sectionHeight,p.x>=gutter {
            if let index=song.sections.indices.dropFirst().first(where:{abs(x(song.sections[$0].start)-p.x)<6}) {
                focusedBoundary=index
                if event.clickCount==2 {store.merge(index);return}
                dragBoundary=index;preview=song.sections;return
            }
            focusedBoundary=nil
            if let section=song.sections.first(where:{time(p.x)>=$0.start && time(p.x)<$0.end}) {store.inspect(section)}
        } else if p.y>=trackY,p.x<gutter {
            let i=Int((p.y-trackY)/trackHeight)
            guard store.visibleTracks.indices.contains(i) else{return}
            let track=store.visibleTracks[i]
            if p.x>=106 {store.audio.toggleSolo(track.id)}else{store.inspectTrack(track)}
        } else if p.x>=gutter {
            var t=time(p.x)
            if event.modifierFlags.contains(.shift),let beat=song.beats.min(by:{abs($0-t)<abs($1-t)}) {t=beat}
            scrubbing=true
            store.audio.beginScrubbing(at:t)
            if p.y>=energyY,p.y<=energyY+energyHeight,let section=song.sections.first(where:{t>=$0.start && t<$0.end}) {store.inspect(section,play:false)}
            else {store.selectedEnergy=nil;store.selected=nil;store.selectedTrack=nil;store.reference=nil}
        }
    }
    func scrubTime(_ point:NSPoint,modifiers:NSEvent.ModifierFlags) -> Double {
        guard let store else{return 0}
        var t=time(point.x)
        if modifiers.contains(.shift),let beat=store.song?.beats.min(by:{abs($0-t)<abs($1-t)}) {t=beat}
        return t
    }
    override func mouseDragged(with event:NSEvent) {
        guard let store,let song=store.song else{return}
        let p=convert(event.locationInWindow,from:nil)
        if let index=dragBoundary {
            preview=SectionEditing.move(song.sections,boundary:index,to:Int64((time(p.x)*songSampleRate).rounded()),snap:store.snap && !event.modifierFlags.contains(.shift),beats:song.beats)
        }else if scrubbing {
            store.audio.scrub(to:scrubTime(p,modifiers:event.modifierFlags))
        }
        needsDisplay=true
    }
    override func mouseUp(with event:NSEvent) {
        if scrubbing {store?.audio.endScrubbing(at:scrubTime(convert(event.locationInWindow,from:nil),modifiers:event.modifierFlags))}
        if let preview,let store {store.save(preview)}
        scrubbing=false;dragBoundary=nil;preview=nil;downPoint=nil;needsDisplay=true
    }
    override func scrollWheel(with event:NSEvent) {
        guard let store else{return}
        if event.modifierFlags.contains(.option) || event.modifierFlags.contains(.command) {
            let p=convert(event.locationInWindow,from:nil)
            store.zoom(exp(Double(event.scrollingDeltaY)*0.015),anchor:max(0,min(1,Double((p.x-gutter)/plotWidth))))
        }else {store.window=store.window.pan(seconds:Double(abs(event.scrollingDeltaX)>abs(event.scrollingDeltaY) ? event.scrollingDeltaX:event.scrollingDeltaY)*store.window.span/Double(plotWidth))}
    }
    override func magnify(with event:NSEvent) {
        let p=convert(event.locationInWindow,from:nil)
        store?.zoom(max(0.1,1+Double(event.magnification)),anchor:max(0,min(1,Double((p.x-gutter)/plotWidth))))
    }
    override func keyDown(with event:NSEvent) {
        guard let store else{return}
        if event.keyCode==120 {store.renaming=store.selected ?? store.current?.id}
        else if event.keyCode==51,let boundary=focusedBoundary {store.merge(boundary);focusedBoundary=nil}
        else if event.keyCode==123 || event.keyCode==124,let boundary=focusedBoundary,let song=store.song {
            let sign:Int64=event.keyCode==123 ? -1:1
            let frame=song.sections[boundary].startFrame+sign*4410
            store.save(SectionEditing.move(song.sections,boundary:boundary,to:frame,snap:false,beats:[]))
        }else if event.keyCode==49 {store.audio.toggle()}
        else {super.keyDown(with:event)}
    }
    override func menu(for event:NSEvent) -> NSMenu? {
        guard let store,let song=store.song else{return nil}
        let p=convert(event.locationInWindow,from:nil)
        guard let section=song.sections.first(where:{time(p.x)>=$0.start && time(p.x)<$0.end}) else{return nil}
        store.inspect(section,play:false)
        let menu=NSMenu()
        for (title,action) in [("Listen from start",#selector(listen)),("Rename section…",#selector(rename)),("Loop section",#selector(loop)),("Compare section",#selector(compare))] {
            let item=NSMenuItem(title:title,action:action,keyEquivalent:"");item.target=self;menu.addItem(item)
        }
        if let index=song.sections.firstIndex(where:{$0.id==section.id}),index>0 {
            let item=NSMenuItem(title:"Merge with previous section",action:#selector(mergeSelected),keyEquivalent:"");item.target=self;menu.addItem(item)
        }
        return menu
    }
    @objc func listen(){if let section=store?.inspected{store?.inspect(section)}}
    @objc func rename(){store?.renaming=store?.selected}
    @objc func loop(){store?.loopSection()}
    @objc func compare(){store?.compare()}
    @objc func mergeSelected(){if let store,let index=store.song?.sections.firstIndex(where:{$0.id==store.selected}){store.merge(index)}}
    func updateAccessibility() {
        setAccessibilityElement(true);setAccessibilityRole(.group);setAccessibilityLabel("Song timeline")
        guard let store,let song=store.song else{return}
        let signature=song.id+song.sections.map{"\($0.id):\($0.name):\($0.startFrame):\($0.endFrame)"}.joined()+store.visibleTracks.map(\.id).joined()+"\(store.window):\(store.audio.solos.sorted()):\(bounds.width):\(bounds.height):\(store.showEnergyChanges)"
        guard signature != accessSignature else{return}
        accessSignature=signature
        var children=song.sections.map { section -> SectionAccessibility in
            let element=accessible[section.id] ?? SectionAccessibility();accessible[section.id]=element;element.timeline=self;element.sectionID=section.id
            element.setAccessibilityEnabled(true)
            element.setAccessibilityRole(.button);element.setAccessibilityLabel("\(section.name), \(songTime(section.start)) to \(songTime(section.end))")
            element.setAccessibilityParent(self)
            element.setAccessibilityFrameInParentSpace(NSRect(x:max(gutter,x(section.start)),y:sectionY,width:max(5,x(section.end)-x(section.start)),height:sectionHeight))
            return element
        }
        for (i,track) in store.visibleTracks.enumerated() {
            for solo in [false,true] {
                let key="track-\(track.id)-\(solo)"
                let element=accessible[key] ?? SectionAccessibility();accessible[key]=element
                element.timeline=self;element.trackID=track.id;element.solo=solo
                element.setAccessibilityRole(.button);element.setAccessibilityEnabled(true)
                element.setAccessibilityLabel(solo ? "Solo \(track.name)":"Inspect \(track.name)")
                element.setAccessibilityValue(solo ? (store.audio.solos.contains(track.id) ? "On":"Off"):"Listen and inspect entrances")
                element.setAccessibilityParent(self)
                element.setAccessibilityFrameInParentSpace(NSRect(x:solo ? 108:14,y:trackY+CGFloat(i)*trackHeight+12,width:solo ? 24:85,height:25))
                children.append(element)
            }
        }
        for c in visibleEnergyChanges {
            let key="energy-"+c.id
            let element=accessible[key] ?? SectionAccessibility();accessible[key]=element
            element.timeline=self;element.change=c;element.setAccessibilityRole(.button);element.setAccessibilityEnabled(true)
            element.setAccessibilityLabel("\(c.title), \(songTime(c.t))")
            element.setAccessibilityParent(self)
            element.setAccessibilityFrameInParentSpace(NSRect(x:x(c.t)-10,y:c.rises ? energyY+energyHeight-22:energyY,width:20,height:22))
            children.append(element)
        }
        let settings=accessible["energy-settings"] ?? SectionAccessibility();accessible["energy-settings"]=settings
        settings.timeline=self;settings.energySettings=true;settings.setAccessibilityRole(.button);settings.setAccessibilityEnabled(true)
        settings.setAccessibilityLabel("Energy display settings");settings.setAccessibilityParent(self)
        settings.setAccessibilityFrameInParentSpace(NSRect(x:10,y:energyY+12,width:115,height:45));children.append(settings)
        setAccessibilityChildren(children)
    }
}
