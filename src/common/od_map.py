"""Self-contained Leaflet views; density renderer needs no CDN/plugin/browser install."""
import json
from collections import Counter
from offline_leaflet import embedded_leaflet_assets, LOCAL_GSI_TILE_TEMPLATE


def map_html(result, zonal=False):
    points = result['points']
    data = dict(days=result['days'], dates=result['dates'], zones=result['zones'],
                origins=dict(result['origins']), destinations=dict(result['destinations']),
                o=[[lat, lon, count] for (lon, lat), count in Counter((p[0], p[1]) for p in points).items()],
                d=[[lat, lon, count] for (lon, lat), count in Counter((p[2], p[3]) for p in points).items()])
    payload = json.dumps(data, ensure_ascii=False).replace('<', '\\u003c')
    return '''<!doctype html><html><head><meta charset="utf-8">''' + embedded_leaflet_assets() + '''
<style>html,body,#map{height:100%;margin:0;font:14px 'Yu Gothic UI',sans-serif;background:#f4f7fa}
.legend{background:rgba(255,255,255,.96);padding:14px 18px;border-radius:10px;box-shadow:0 3px 18px #1233;line-height:1.7;max-width:380px;color:#163047}
.legend strong{font-size:17px}.bar{height:10px;margin:8px 0}.leaflet-tooltip{font:13px 'Yu Gothic UI',sans-serif}</style></head><body><div id="map"></div><script>
const data=''' + payload + ', zonal=' + json.dumps(zonal) + ', localUrl=' + json.dumps(LOCAL_GSI_TILE_TEMPLATE) + ''';
const map=L.map('map',{preferCanvas:true}).setView([35.07,133.93],11);
addGsiOfflineLayer(map,{localUrl:localUrl,preferLocal:true});
let settings={side:'o',radius:24,blur:0.65,gain:1,opacity:0.75,max:0,palette:'暖色',log:false,labels:true};
const palettes={'暖色':['#fff3c4','#ffbd54','#f27035','#b51932'],'寒色':['#e6f5ff','#78c8df','#2985b9','#18377c'],'緑':['#edf8dc','#a9d18e','#45a17a','#125949']};
function color(t){const colors=palettes[settings.palette],x=Math.max(0,Math.min(1,t))*3,i=Math.min(2,Math.floor(x)),f=x-i;
const a=colors[i].slice(1).match(/../g).map(x=>parseInt(x,16)),b=colors[i+1].slice(1).match(/../g).map(x=>parseInt(x,16));
return a.map((v,k)=>Math.round(v+(b[k]-v)*f));}
function esc(s){return String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
const legend=L.control({position:'bottomright'});legend.onAdd=()=>{const d=L.DomUtil.create('div','legend');d.id='legend';L.DomEvent.disableClickPropagation(d);return d};legend.addTo(map);
let polygons=L.layerGroup().addTo(map), canvas=document.createElement('canvas');
canvas.style.cssText='position:absolute;pointer-events:none;z-index:450';map.getContainer().appendChild(canvas);
function render(){
window.odReady=false;
polygons.clearLayers();const size=map.getSize();canvas.width=size.x;canvas.height=size.y;const ctx=canvas.getContext('2d');ctx.clearRect(0,0,size.x,size.y);
const values=settings.side==='o'?data.origins:data.destinations, pts=settings.side==='o'?data.o:data.d;
let maximum=1;
if(zonal){
maximum=settings.max||data.zones.reduce((m,z)=>Math.max(m,(values[z.name]||0)/data.days),1);
for(const z of data.zones){const value=(values[z.name]||0)/data.days,t=settings.log?Math.log1p(value)/Math.log1p(maximum):value/maximum;
const c=value===0?'#edf0f3':'rgb('+color(t).join(',')+')';
const p=L.polygon(z.points.map(p=>[p[1],p[0]]),{color:'#475569',weight:1,fillColor:c,fillOpacity:settings.opacity}).addTo(polygons);
p.bindTooltip(esc(z.name)+'<br>'+value.toFixed(2)+' トリップ/日',{permanent:settings.labels,direction:'center',className:'zone-label'});}
}else{
const field=document.createElement('canvas');field.width=size.x;field.height=size.y;const f=field.getContext('2d');f.globalCompositeOperation='lighter';
maximum=settings.max||pts.reduce((m,p)=>Math.max(m,p[2]/data.days),1);
const radius=settings.radius;
for(const p of pts){const q=map.latLngToContainerPoint([p[0],p[1]]);if(q.x< -radius||q.y< -radius||q.x>size.x+radius||q.y>size.y+radius)continue;
const alpha=Math.min(1,p[2]/data.days/maximum*settings.gain),g=f.createRadialGradient(q.x,q.y,radius*(1-settings.blur)*0.7,q.x,q.y,radius);
g.addColorStop(0,'rgba(0,0,0,'+alpha+')');g.addColorStop(1,'rgba(0,0,0,0)');f.fillStyle=g;f.fillRect(q.x-radius,q.y-radius,radius*2,radius*2);}
const img=f.getImageData(0,0,size.x,size.y);for(let i=0;i<img.data.length;i+=4){const a=img.data[i+3]/255;if(!a)continue;const c=color(a);img.data[i]=c[0];img.data[i+1]=c[1];img.data[i+2]=c[2];img.data[i+3]=Math.round(Math.min(1,a*2)*settings.opacity*255);}ctx.putImageData(img,0,0);
}
const kind=settings.side==='o'?'起点・発生':'終点・集中';
document.getElementById('legend').innerHTML='<strong>'+(zonal?'ゾーン別発生・集中マップ':'ODヒートマップ')+'</strong><br>'+kind+' ｜ '+data.days+'日平均<br>'+data.dates[0]+' – '+data.dates[data.dates.length-1]+'<div class="bar" style="background:linear-gradient(to right,'+palettes[settings.palette].join(',')+')"></div>'+(zonal?'0 → '+maximum.toFixed(2)+' トリップ/日'+(settings.log?'（対数配色）':''):'低密度 → 高密度（相対表示）')+'<br><small>'+(zonal?'区域外・重複ゾーンはOD表に別掲':'表示倍率・半径・ぼかしで濃淡が変わります')+'</small>';
window.odReady=true;
}
window.updateOD=function(s){Object.assign(settings,s);render();};
map.on('moveend zoomend resize',render);
let bounds=[];if(zonal){for(const z of data.zones)bounds.push(...z.points.map(p=>[p[1],p[0]]));}else{bounds=[...data.o,...data.d].map(p=>[p[0],p[1]]);}
if(bounds.length)map.fitBounds(bounds,{padding:[30,30],maxZoom:14});render();
</script></body></html>'''
