import json,sys
d=open(sys.argv[1]).read()
open('endzone_lab.html','w').write(open('template.html').read().replace('__DATA__',d))
