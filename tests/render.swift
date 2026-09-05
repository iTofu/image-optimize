import AppKit
// usage: render <in.svg> <out.png> <pixelsPerPoint>
let a = CommandLine.arguments
guard a.count >= 4, let img = NSImage(contentsOfFile: a[1]) else { FileHandle.standardError.write("load fail\n".data(using: .utf8)!); exit(1) }
let scale = CGFloat(Double(a[3]) ?? 4)
let w = Int(img.size.width * scale), h = Int(img.size.height * scale)
guard let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: w, pixelsHigh: h, bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0) else { exit(2) }
NSGraphicsContext.saveGraphicsState()
NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)
NSGraphicsContext.current?.imageInterpolation = .high
img.draw(in: NSRect(x: 0, y: 0, width: w, height: h), from: .zero, operation: .sourceOver, fraction: 1)
NSGraphicsContext.restoreGraphicsState()
try! rep.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: a[2]))
print("\(w)x\(h)")
