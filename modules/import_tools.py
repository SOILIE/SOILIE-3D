import numpy as np
import re
from urllib2 import urlopen
from PIL import Image
import cStringIO

def readValuesFromTxt(filename,local):
    if local: 
        fid = open(filename,'r')
        values = [map(lambda y: float(y), x.split(' ')) for x in \
		 ''.join(fid.read().decode('utf-8')).split('\n') if x!='']
        fid.close()
    else:
	values = [map(lambda y: float(y), x.split(' ')) for x in \
		 ''.join(urlopen(filename).read().decode('utf-8')).split('\n') \
		 if x!='']
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
        depth = Image.open(cStringIO.StringIO(urlopen(filename).read()))
    depth = np.array(depth)
    bitshift = np.vectorize(lambda d: (d >> 3) or (d << (16-3)),otypes=[np.float])
    div = np.vectorize(lambda d: float(d)/float(1000),otypes=[np.float])
    depth = div(bitshift(depth))
    return depth.tolist()

def depth2XYZcamera(K,depth):
    [x,y] = np.meshgrid(range(1,641),range(1,481))
    XYZcamera = []
    op_apply = np.vectorize(lambda op,n1,n2: eval(str(n1)+str(op)+str(n2)))
    XYZcamera.append(np.multiply(op_apply('-',x,K[0][2]),\
                                 op_apply('/',depth,K[0][0])))
    XYZcamera.append(np.multiply(op_apply('-',y,K[1][2]),\
                                 op_apply('/',depth,K[1][1])))
    XYZcamera.append(depth)
    XYZcamera.append(np.vectorize(lambda n: int(bool(n)))(depth))
    XYZcamera = np.swapaxes(np.swapaxes(XYZcamera,0,2),0,1).tolist()
    return XYZcamera

def camera2XYZworld(XYZcamera,extrinsics):
    XYZ = []
    valid = np.swapaxes(np.swapaxes(XYZcamera,0,1),0,2).tolist()[3]
    
    for j in range(0,len(valid[0])):
        for i in range(0,len(valid)):
            if valid[i][j]:
                XYZ.append([XYZcamera[i][j][0],\
                            XYZcamera[i][j][1],\
                            XYZcamera[i][j][2]])
    XYZworld = transformPointCloud(XYZ,extrinsics)
    return [XYZworld, valid]

def transformPointCloud(XYZ,Rt):
    Rt = [[float(x[0]),float(x[1]),float(x[2])] for x in Rt]
    return np.matmul(Rt,np.transpose(XYZ)).tolist()
