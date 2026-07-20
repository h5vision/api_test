import os, zipfile, re, xml.etree.ElementTree as ET

files = ['VS Code AI Code Assistant API 1&1.docx', 'VS Code AI Code Assistant API uni.docx']
for name in files:
    path = os.path.join(os.getcwd(), name)
    print('===== ' + name + ' =====')
    with zipfile.ZipFile(path) as z:
        data = z.read('word/document.xml')
    root = ET.fromstring(data)
    ns = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
    texts = []
    for t in root.findall('.//w:t', ns):
        if t.text:
            texts.append(t.text)
    text = ''.join(texts)
    text = re.sub(r'\s+', ' ', text)
    print(text[:20000])
    print('\n')
