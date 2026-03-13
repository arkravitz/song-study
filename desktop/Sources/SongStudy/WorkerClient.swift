import Foundation

struct AppConfiguration: Codable {
    var root:String
    var legacy:String
    var store:String
    var python:String
    var backend:String
    var worker:String
    var tools:String?
}
struct WorkerFailure: LocalizedError {
    let message:String
    var errorDescription:String? {message}
}

@MainActor final class WorkerClient {
    private var process:Process?
    private var input:FileHandle?
    private var pending:[String:CheckedContinuation<Data,Error>]=[:]
    var progress:((String,Double)->Void)?
    func start() throws {
        guard process==nil else{return}
        let resources=Bundle.main.resourceURL ?? URL(fileURLWithPath:FileManager.default.currentDirectoryPath)
        let configURL=resources.appendingPathComponent("configuration.json")
        let config=try JSONDecoder().decode(AppConfiguration.self,from:Data(contentsOf:configURL))
        let support=FileManager.default.urls(for:.applicationSupportDirectory,in:.userDomainMask).first!
        let expand:(String)->String={ $0.replacingOccurrences(of:"$RESOURCES",with:resources.path).replacingOccurrences(of:"$APPLICATION_SUPPORT",with:support.path) }
        let store=expand(config.store)
        try FileManager.default.createDirectory(atPath:store,withIntermediateDirectories:true)
        let p=Process();p.executableURL=URL(fileURLWithPath:expand(config.python))
        p.arguments=["-u",expand(config.worker),"--root",expand(config.root),"--legacy",expand(config.legacy),"--store",store]
        p.currentDirectoryURL=URL(fileURLWithPath:FileManager.default.fileExists(atPath:expand(config.root)) ? expand(config.root):resources.path)
        var env=ProcessInfo.processInfo.environment
        env["PYTHONPATH"]=expand(config.backend)
        env["PYTHONDONTWRITEBYTECODE"]="1"
        env["PATH"]=(config.tools.map(expand) ?? "/opt/homebrew/bin")+":/usr/bin:/bin"
        env["DEEPSEEK_API_KEY"]=""
        p.environment=env
        let incoming=Pipe(),outgoing=Pipe();p.standardInput=incoming;p.standardOutput=outgoing
        let log=URL(fileURLWithPath:store).appendingPathComponent("worker.log")
        if !FileManager.default.fileExists(atPath:log.path){FileManager.default.createFile(atPath:log.path,contents:nil)}
        let error=try FileHandle(forWritingTo:log);try error.seekToEnd();p.standardError=error
        try p.run();process=p;input=incoming.fileHandleForWriting
        let workerPID=p.processIdentifier
        let handle=outgoing.fileHandleForReading
        DispatchQueue(label:"SongStudy.worker.responses",qos:.userInitiated).async { [weak self] in
            var buffer=Data()
            while true {
                let chunk=handle.availableData
                if chunk.isEmpty {break}
                buffer.append(chunk)
                while let edge=buffer.firstIndex(of:10) {
                    let line=buffer.prefix(upTo:edge);buffer.removeSubrange(...edge)
                    guard let object=try? JSONSerialization.jsonObject(with:line) as? [String:Any] else{continue}
                    DispatchQueue.main.async { self?.receive(object) }
                }
            }
            DispatchQueue.main.async { if self?.process?.processIdentifier==workerPID {self?.failed("The analysis worker stopped. Reopen the app to reconnect; completed songs and edits are saved.")} }
        }
    }
    func request<T:Decodable>(_ method:String,params:[String:Any]=[:],as:T.Type) async throws -> T {
        try start()
        let id=UUID().uuidString
        let data=try JSONSerialization.data(withJSONObject:["id":id,"method":method,"params":params])
        let response:Data=try await withCheckedThrowingContinuation {continuation in
            pending[id]=continuation
            do {try input?.write(contentsOf:data+Data([10]))}
            catch {pending.removeValue(forKey:id)?.resume(throwing:error)}
        }
        return try JSONDecoder().decode(T.self,from:response)
    }
    private func receive(_ object:[String:Any]) {
        guard let id=object["id"] as? String else{return}
        if let message=object["progress"] as? String, pending[id] != nil {progress?(message,object["fraction"] as? Double ?? 0);return}
        guard let continuation=pending.removeValue(forKey:id) else{return}
        if let error=object["error"] as? String {continuation.resume(throwing:WorkerFailure(message:error))}
        else if let value=object["result"],let data=try? JSONSerialization.data(withJSONObject:value,options:.fragmentsAllowed) {continuation.resume(returning:data)}
        else {continuation.resume(throwing:WorkerFailure(message:"The analysis response could not be read."))}
    }
    private func failed(_ message:String) {
        for continuation in pending.values {continuation.resume(throwing:WorkerFailure(message:message))}
        pending.removeAll();process=nil;input=nil
    }
    func terminate(){if let process {if kill(-process.processIdentifier,SIGTERM) != 0 {process.terminate()}};failed("Analysis canceled.")}
}
