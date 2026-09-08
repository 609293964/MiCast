"""Check native Windows icon rendering, DIB masks, and the deployed touch icon."""
from pathlib import Path
import struct
import sys
import win32gui, win32ui, win32con
from PIL import Image, ImageDraw

root=Path(__file__).resolve().parent.parent
path=root/'assets/icons/windows/micast.ico'
raw=path.read_bytes()
sizes=[]
for i in range(struct.unpack_from('<H',raw,4)[0]):
    w,h,_,_,_,bits,length,offset=struct.unpack_from('<BBBBHHII',raw,6+16*i)
    w=w or 256; h=h or 256
    assert bits==32 and struct.unpack_from('<I',raw,offset)[0]==40
    stride=((w+31)//32)*4
    mask=raw[offset+40+w*h*4:offset+length]
    assert len(mask)==stride*h
    for y in range(h):
        for x in range(w):
            alpha=raw[offset+40+(y*w+x)*4+3]
            if w<=48: assert alpha in (0,255), 'small shell frame has fractional alpha'
            assert bool(mask[y*stride+x//8] & (128>>(x%8))) == (alpha==0)
    sizes.append(w)

board=Image.new('RGB',(760,300),'#eeeeee')
d=ImageDraw.Draw(board)
for row,bg in enumerate([0x202020,0xFFFFFF]):
    x=12
    for size in [16,24,32,48,64]:
        dc=win32ui.CreateDCFromHandle(win32gui.GetDC(0))
        mem=dc.CreateCompatibleDC()
        bitmap=win32ui.CreateBitmap();bitmap.CreateCompatibleBitmap(dc,size,size)
        mem.SelectObject(bitmap);mem.FillSolidRect((0,0,size,size),bg)
        icon=win32gui.LoadImage(0,str(path),win32con.IMAGE_ICON,size,size,win32con.LR_LOADFROMFILE)
        win32gui.DrawIconEx(mem.GetSafeHdc(),0,0,icon,size,size,0,0,win32con.DI_NORMAL)
        im=Image.frombytes('RGB',(size,size),bitmap.GetBitmapBits(True),'raw','BGRX',0,1)
        assert im.getpixel((0,0))==((32,32,32) if row==0 else (255,255,255))
        source=Image.open(root/f'assets/icons/windows/micast-{size}.png').convert('RGBA')
        expected=Image.alpha_composite(Image.new('RGBA',source.size,(32,32,32,255) if row==0 else (255,255,255,255)),source)
        assert max(abs(a-b) for actual,wanted in zip(im.getdata(),expected.convert('RGB').getdata()) for a,b in zip(actual,wanted))<=2, 'native drawing differs from expected alpha composite'
        board.paste(im.resize((size*2,size*2),Image.Resampling.NEAREST),(x,row*150+20))
        x+=size*2+20
        win32gui.DestroyIcon(icon);mem.DeleteDC();win32gui.DeleteObject(bitmap.GetHandle())
        win32gui.ReleaseDC(0,dc.GetSafeHdc())
(root/'.run').mkdir(exist_ok=True)
board.save(root/'.run/icon-native-preview.png')
for p in [root/'assets/icons/web/apple-touch-icon.png',root/'web/public/icons/apple-touch-icon.png']:
    im=Image.open(p)
    assert im.mode=='RGB' and im.size==(180,180)
    assert all(im.getpixel(c)==(185,75,54) for c in [(0,0),(179,0),(0,179),(179,179)])
print('PASS: ICO masks and native dark/light corner rendering; opaque RGB touch icons.',sizes)
if '--exe' in sys.argv:
    import pefile
    pe=pefile.PE(str(root/'dist/MiCast.exe'))
    resources=[]
    for kind in pe.DIRECTORY_ENTRY_RESOURCE.entries:
        if kind.id==3:  # RT_ICON
            for entry in kind.directory.entries:
                for language in entry.directory.entries:
                    info=language.data.struct
                    resources.append(pe.get_data(info.OffsetToData,info.Size))
    for i in range(len(sizes)):
        length,offset=struct.unpack_from('<II',raw,6+16*i+8)
        assert raw[offset:offset+length] in resources, 'EXE contains stale icon data'
    pe.close()
    print('PASS: every generated icon frame is embedded unchanged in dist/MiCast.exe')
