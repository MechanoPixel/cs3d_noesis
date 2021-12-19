from inc_noesis import *

# Modified on 7/20/21 (3:05 AM)
# This is here so it's easier to recognize later versions
# without having to deal with version numbers

# Willow's unreadable garbage
def readInt32(fp):
  return int.from_bytes(fp.read(4), byteorder='little', signed=True)

def readString(inputBytes): # reads null terminated string from bytes object
  output = ''
  for i in range(0, len(inputBytes)):
    if inputBytes[i] == 0:
      return output
    output += chr(inputBytes[i])
  return output

def getSegmentThatEndsWith(inputObject, endsWithString): # terrible bodge while I work out something better
  for attr, value in inputObject.items():
    if attr.endswith(endsWithString):
      return value

def getN3DSegments(basePath):
  outputData = {}
  with open(basePath+'.n3dhdr', 'rb') as hdrFilePointer:
    # Retrieve amount of segments in file (this offset is pretty much constant)
    hdrFilePointer.seek(256, 0)
    totalSegments = readInt32(hdrFilePointer)

    dtaFilePointer = open(basePath+'.n3ddta', 'rb')
    objectHasSkeleton = False
    objectName = ''
    for segmentIndex in range(0, totalSegments):
      # Layout: UNKNOWN (4 bytes), Offset (4 bytes), Length (4 bytes)
      hdrFilePointer.seek(4, 1) # skip unknown
      segmentOffset = readInt32(hdrFilePointer) # read offset from n3dhdr
      segmentLength = readInt32(hdrFilePointer) # read length from n3dhdr

      dtaFilePointer.seek(segmentOffset, 0)
      segmentData = dtaFilePointer.read(segmentLength) # read data from n3ddta

      segmentName = ''
      if segmentIndex == 0: # check if n3ddta has valid skeleton info
        objectName = readString(segmentData)
        skeletonName = readString(segmentData[256:])
        skinName = readString(segmentData[384:])
        if skeletonName.startswith(objectName) and skinName.startswith(objectName):
          objectHasSkeleton = True
        outputData['hasSkeleton'] = objectHasSkeleton

      if segmentIndex == 1 and objectHasSkeleton: # skeleton info offset starts with joint count
        segmentName = objectName + '-skeleton' # because of that, we need to name it manually
      else:
        segmentName = readString(segmentData) # if there's no skeleton data it starts with segment name

      outputData[segmentName] = {'offset': segmentOffset, 'length': segmentLength}

    hdrFilePointer.close()
    dtaFilePointer.close()
  return outputData
# End of Willow's unreadable garbage

def registerNoesisTypes():
  handle = noesis.register("Cave Story 3D Data",".n3ddta")
  noesis.setHandlerTypeCheck(handle, CheckType)
  noesis.setHandlerLoadModel(handle, LoadModel)  
  return 1
  
def CheckType(data):
  bs = NoeBitStream(data)
  return 1

def Align(bs, n):
  value = bs.tell() % n
  if (value):
    bs.seek(n - value, 1)

def LoadModel(data, mdlList):
  ctx = rapi.rpgCreateContext()
  bs = NoeBitStream(data)
  bs.setEndian(NOE_LITTLEENDIAN)

  currentFilePath = noesis.getSelectedFile()
  baseFilePath = currentFilePath[:-7]
  n3dSegments = getN3DSegments(baseFilePath)
  
  if n3dSegments['hasSkeleton']:
    #jump to skel section, grab names and parenting info
    jointNames = []
    jointParents = []
    jointMatrices = []
    skeletonSegmentOffset = getSegmentThatEndsWith(n3dSegments, 'skeleton')['offset'];
    bs.seek(skeletonSegmentOffset)
    jointCount = bs.readUInt()
    unk = bs.readUInt()
    for i in range(jointCount):
      jointNames.append(bs.readString())
      Align(bs,4)
      #very annoying alignment going on, cheat and jump to 4 directly, seems consistent
      a = 0
      while(a - 4):
        a = bs.readUInt()
      if i:#skip 0x70 bytes for root and 0x58 for others to jump to parent info directly.
        bs.seek(0x50,1)
      else:
        bs.seek(0x68,1) 
      jointParents.append(bs.readByte())
      Align(bs,4)
      
    #jump to bone bind transform section
    boneBindSegmentOffset = getSegmentThatEndsWith(n3dSegments, 'skin')['offset'];
    bs.seek(boneBindSegmentOffset+(256*3)+80)
    #bs.seek(0x17F0)
    for _ in range(jointCount):
      jointMatrices.append(NoeMat44.fromBytes(bs.readBytes(0x40)).toMat43().inverse())
    
    #we have all the bone info, constructing the skeleton
    jointList = []
    for i, (parent,name, mat) in enumerate(zip(jointParents,jointNames,jointMatrices)):
      joint = NoeBone(i, name, mat, None, parent)
      jointList.append(joint)

  #mesh section
  meshSectionOffset = getSegmentThatEndsWith(n3dSegments, 'mesh')['offset'];
  bs.seek(meshSectionOffset+292)
  vCount, idxCount, _, idxOffs, vOffs = bs.readUInt(),bs.readUInt(),bs.readUInt(),bs.readUInt(),bs.readUInt()
  vStride = 0x28
  
  #vertices
  bs.seek(meshSectionOffset + vOffs)
  vBuffer = bs.readBytes(vCount * vStride)
  rapi.rpgClearBufferBinds()
  rapi.rpgBindPositionBufferOfs(vBuffer, noesis.RPGEODATA_FLOAT, vStride,0x0)
  rapi.rpgBindNormalBufferOfs(vBuffer, noesis.RPGEODATA_FLOAT, vStride,0x10)
  rapi.rpgBindUV1BufferOfs(vBuffer, noesis.RPGEODATA_FLOAT, vStride,0x1C)
  rapi.rpgBindUV1BufferOfs(vBuffer, noesis.RPGEODATA_FLOAT, vStride,0x1C)
  rapi.rpgBindBoneIndexBufferOfs(vBuffer, noesis.RPGEODATA_UBYTE, vStride,0x24, 0x1)
  wBuffer = b'x\FF' * vCount #create dummy wBuffer of weight 1 
  rapi.rpgBindBoneWeightBuffer(wBuffer, noesis.RPGEODATA_UBYTE, 0x1, 0x1)
  
  #indices
  bs.seek(meshSectionOffset + idxOffs)
  idxBuffer = bs.readBytes(idxCount * 2)
  rapi.rpgCommitTriangles(idxBuffer,noesis.RPGEODATA_USHORT, idxCount,noesis.RPGEO_TRIANGLE)    
  
  try:
    mdl = rapi.rpgConstructModel()
  except:
    mdl = NoeModel()
  rapi.setPreviewOption("setAngOfs", "0 -90 0")
  if n3dSegments['hasSkeleton']:
    mdl.setBones(jointList)
  mdlList.append(mdl)
  
  return 1
  #credit to Dimy#0617 for building the script and being a great help
  
