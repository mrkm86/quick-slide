// 音符の一覧（events.json）を、macOS 内蔵の General MIDI 音源（Apple DLS シンセ）で鳴らして WAV に書き出す。
// インストール不要。  swift render.swift <events.json> <out.wav>
import AVFoundation

struct Ev: Decodable { let t: Double; let k: String; let c: Int; let a: Int; let b: Int }
let args = CommandLine.arguments
let evs = try JSONDecoder().decode([Ev].self, from: Data(contentsOf: URL(fileURLWithPath: args[1])))

let engine = AVAudioEngine()
let desc = AudioComponentDescription(componentType: kAudioUnitType_MusicDevice, componentSubType: kAudioUnitSubType_DLSSynth,
                                     componentManufacturer: kAudioUnitManufacturer_Apple, componentFlags: 0, componentFlagsMask: 0)
let synth = AVAudioUnitMIDIInstrument(audioComponentDescription: desc)
let reverb = AVAudioUnitReverb()
reverb.loadFactoryPreset(.largeHall2)
reverb.wetDryMix = 32
engine.attach(synth); engine.attach(reverb)
let sr = 44100.0
let fmt = AVAudioFormat(standardFormatWithSampleRate: sr, channels: 2)!
engine.connect(synth, to: reverb, format: fmt)
engine.connect(reverb, to: engine.mainMixerNode, format: fmt)
try engine.enableManualRenderingMode(.offline, format: fmt, maximumFrameCount: 4096)
try engine.start()

let out = try AVAudioFile(forWriting: URL(fileURLWithPath: args[2]), settings: engine.manualRenderingFormat.settings)
let buf = AVAudioPCMBuffer(pcmFormat: engine.manualRenderingFormat, frameCapacity: engine.manualRenderingMaximumFrameCount)!
let step: AVAudioFrameCount = 64            // 1.5 ミリ秒ごとに音符を流し込む
let total = AVAudioFramePosition(((evs.map { $0.t }.max() ?? 0) + 4.5) * sr)
var i = 0
while engine.manualRenderingSampleTime < total {
    let now = Double(engine.manualRenderingSampleTime) / sr
    while i < evs.count && evs[i].t <= now {
        let e = evs[i], ch = UInt8(e.c)
        switch e.k {
        case "on":  synth.startNote(UInt8(e.a), withVelocity: UInt8(e.b), onChannel: ch)
        case "off": synth.stopNote(UInt8(e.a), onChannel: ch)
        case "pc":  synth.sendProgramChange(UInt8(e.a), onChannel: ch)
        case "cc":  synth.sendController(UInt8(e.a), withValue: UInt8(e.b), onChannel: ch)
        default: break
        }
        i += 1
    }
    let st = try engine.renderOffline(step, to: buf)
    guard st == .success else { print("render status", st.rawValue); break }
    try out.write(from: buf)
}
print("rendered", Double(engine.manualRenderingSampleTime) / sr, "sec")
