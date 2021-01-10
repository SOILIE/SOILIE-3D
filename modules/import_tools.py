import numpy as np
import gc
import re
import urllib.request
from io import BytesIO
from PIL import Image

def readValuesFromTxt(filename,local):
    if local: 
        fid = open(filename,'r')
        values = [map(lambda y: float(y), x.split(' ')) for x in \
		 ''.join(fid.read().decode('utf-8')).split('\n') if x!='']
        fid.close()
    else:
        fid = urllib.request.urlopen(filename)
        file_utf = fid.read().decode('utf-8')
        values = [list(map(lambda y: float(y), x.split(' '))) for x in \
		 ''.join(file_utf).split('\n') if x!='']
        fid.close()
    return values

def getExtrinsics(extrinsicsC2W,frame):
    extrinsics = [[extrinsicsC2W[0][0][frame],extrinsicsC2W[0][1][frame], \
                   extrinsicsC2W[0][2][frame],extrinsicsC2W[0][3][frame]],\
                  [extrinsicsC2W[1][0][frame],extrinsicsC2W[1][1][frame], \
                   extrinsicsC2W[1][2][frame],extrinsicsC2W[1][3][frame]],\
                  [extrinsicsC2W[2][0][frame],extrinsicsC2W[2][1][frame], \
                   extrinsicsC2W[2][2][frame],extrinsicsC2W[2][3][frame]]]
    return extrinsics

def depthRead(filename,local):
    if local:
        depth = Image.open(filename,'r')
    else:
        depth = Image.open(BytesIO(urllib.request.urlopen(filename).read()))
    depthmap = np.array(depth)
    depth.close()
    bitshift = [[(d >> 3) or (d << 16-3) for d in row] for row in depthmap]
    depthmap = [[float(d)/1000.0 for d in row] for row in bitshift]
    gc.collect()
    #operation = lambda d: (d >> 3) or (d << 16-3)
    #bitshift = np.vectorize(operation,otypes=[np.float])
    #depthmap = bitshift(depthmap)
    #operation = lambda d: float(d)/1000.0
    #div = np.vectorize(operation,otypes=[np.float])
    #depthmap = div(depthmap)
    #gc.collect()
    return depthmap

def depth2XYZcamera(K,depth):
    [x,y] = np.meshgrid(range(1,641),range(1,481))
    XYZcamera = []
    #operation = lambda op,n1,n2: eval(str(n1)+str(op)+str(n2))
    #op_apply = np.vectorize(operation)
    l = np.multiply([[d - K[0][2] for d in row] for row in x], \
                    [[d / K[0][0] for d in row] for row in depth])
    #x = None
    #l = np.multiply(op_apply('-',x,K[0][2]), \
    #                op_apply('/',depth,K[0][0]))
    XYZcamera.append(l)
    l = np.multiply([[d - K[1][2] for d in row] for row in y], \
                    [[d / K[1][1] for d in row] for row in depth])
    #l = np.multiply(op_apply('-',y,K[1][2]), \
    #                op_apply('/',depth,K[1][1]))
    XYZcamera.append(l)
    #l = y = x = None
    #gc.collect()
    XYZcamera.append(depth)
    #operation = lambda(n): int(bool(n))
    #op_apply = np.vectorize(operation)
    #depth = op_apply(depth)
    depth = [[int(bool(n)) for n in row] for row in depth]
    #operation = None
    #op_apply = None
    XYZcamera.append(depth)
    #depth = None
    #XYZcamera = np.swapaxes(XYZcamera,0,2)
    #XYZcamera = np.swapaxes(XYZcamera,0,1)
    #gc.collect()
    return XYZcamera

def camera2XYZworld(XYZcamera,extrinsics):
    XYZ = []
    #print "XYZcamera:",len(XYZcamera),len(XYZcamera[0]),len(XYZcamera[0][0])
    for j in range(0,len(XYZcamera[0][0])):
        for i in range(0,len(XYZcamera[0])):
            if XYZcamera[3][i][j]:
                data = (XYZcamera[0][i][j],\
                        XYZcamera[1][i][j],\
                        XYZcamera[2][i][j])
                XYZ.append(data)
    #print "XYZ:",len(XYZ),len(XYZ[0])
    XYZworld = transformPointCloud(XYZ,extrinsics)
    vals = (XYZworld,XYZcamera[3])
    return vals

def transformPointCloud(XYZ,Rt):
    Rt = [(float(t[0]),float(t[1]),float(t[2])) for t in Rt]
    return np.matmul(Rt,np.transpose(XYZ))
