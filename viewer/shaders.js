// Projection shader for equirectangular panoramas.
//
// Every viewing mode is the same operation: for each screen pixel work out which
// direction in the world it looks at, then sample the equirectangular texture. Doing
// it that way -- rather than swapping meshes per mode -- means the modes can be
// blended into one another continuously, which is what makes the little-planet
// "dolly" feel like one camera move instead of a hard cut between two renderers.

export const VERT = /* glsl */ `
out vec2 vUv;
void main() {
  vUv = uv;
  gl_Position = vec4(position.xy, 0.0, 1.0);
}
`;

export const FRAG = /* glsl */ `
precision highp float;

in vec2 vUv;
out vec4 fragColor;

uniform sampler2D uPano;
uniform mat3  uCamRot;        // view -> world
uniform float uAspect;
uniform float uFov;           // radians, VERTICAL field: x is scaled by uAspect, so the
                              // horizontal field is 2*atan(aspect*tan(fov/2)). Mirrored
                              // exactly by panolib/project.View -- keep them in step.
uniform int   uMode;
uniform float uMorph;         // 0 = rectilinear, 1 = stereographic
uniform vec2  uLonRange;      // covered longitude (radians); full sphere = (-PI, PI)
uniform vec2  uLatRange;      // covered latitude  (radians); full sphere = (-PI/2, PI/2)
uniform float uExposure;
uniform float uGlobeDist;     // camera distance for the globe mode, in sphere radii
uniform vec3  uBackground;
uniform float uVignette;
uniform float uPanniniD;

const float PI = 3.14159265358979;

// ---------------------------------------------------------------- sampling

// Equirect lookup. The +/-180 seam needs care: screen-space derivatives jump by a
// whole texture width there, so an automatic mipmap level collapses to the blurriest
// one and draws a visible vertical band. Unwrapping the derivative before calling
// textureGrad keeps the seam invisible while still allowing mipmapped minification.
vec4 samplePano(vec3 dir, out bool inside) {
  float lon = atan(dir.x, dir.z);
  float lat = asin(clamp(dir.y, -1.0, 1.0));

  float span = max(uLonRange.y - uLonRange.x, 1e-4);
  float t = fract((lon - uLonRange.x) / (2.0 * PI));
  float u = t * (2.0 * PI / span);

  float vspan = max(uLatRange.y - uLatRange.x, 1e-4);
  float v = (uLatRange.y - lat) / vspan;

  inside = (u >= 0.0 && u <= 1.0 && v >= 0.0 && v <= 1.0);
  if (!inside) return vec4(uBackground, 1.0);

  vec2 uv = vec2(u, v);
  vec2 dx = dFdx(uv);
  vec2 dy = dFdy(uv);
  if (abs(dx.x) > 0.5) dx.x -= sign(dx.x);
  if (abs(dy.x) > 0.5) dy.x -= sign(dy.x);
  return textureGrad(uPano, uv, dx, dy);
}

// ---------------------------------------------------------------- projections

vec3 dirRectilinear(vec2 p, float fov) {
  float t = tan(clamp(fov, 0.01, 3.0) * 0.5);
  return normalize(vec3(p.x * t, p.y * t, 1.0));
}

// Stereographic. Radius on screen maps to angle from the axis as
// theta = 2*atan(r * tan(fov/4)), so the screen edge sits exactly at fov/2.
// This one projection covers little planet, the zenith tunnel and plain fisheye --
// they differ only in where the camera points and how wide the field is.
vec3 dirStereographic(vec2 p, float fov) {
  float r = length(p);
  if (r < 1e-6) return vec3(0.0, 0.0, 1.0);
  float theta = 2.0 * atan(r * tan(clamp(fov, 0.01, 6.0) * 0.25));
  vec2 d = p / r;
  return vec3(sin(theta) * d.x, sin(theta) * d.y, cos(theta));
}

// Pannini: cylindrical in the horizontal, perspective in the vertical, so vertical
// lines stay vertical while the field of view goes much wider than rectilinear can.
// Useful for the architectural and canyon shots where a rectilinear wide angle
// smears the edges.
vec3 dirPannini(vec2 p, float fov, float d) {
  float s = tan(clamp(fov, 0.01, 3.0) * 0.5);
  float x = p.x * s * (d + 1.0);
  float y = p.y * s * (d + 1.0);

  float u = x / (d + 1.0);
  float arg = u * d / sqrt(1.0 + u * u);
  if (abs(arg) > 1.0) return vec3(0.0);           // outside the projection's domain
  float phi = atan(u) + asin(arg);

  float S = (d + 1.0) / (d + cos(phi));
  float theta = atan(y / max(S, 1e-4));
  return vec3(sin(phi) * cos(theta), sin(theta), cos(phi) * cos(theta));
}

vec3 dirMercator(vec2 p, float zoom) {
  float lon = p.x * PI * zoom;
  float lat = 2.0 * atan(exp(p.y * PI * zoom)) - PI * 0.5;
  return vec3(cos(lat) * sin(lon), sin(lat), cos(lat) * cos(lon));
}

vec3 dirEquirectFlat(vec2 p, float zoom) {
  float lon = p.x * PI * zoom;
  float lat = p.y * PI * 0.5 * zoom;
  return vec3(cos(lat) * sin(lon), sin(lat), cos(lat) * cos(lon));
}

// Mirror ball: the whole sphere squeezed into a disc, the way a chrome ball reflects
// its surroundings. A surface point at screen radius r has its normal asin(r) off
// axis, and the reflected ray is twice that.
vec3 dirMirrorBall(vec2 p, out bool hit) {
  float r = length(p);
  hit = r <= 1.0;
  if (!hit) return vec3(0.0, 0.0, 1.0);
  float theta = 2.0 * asin(clamp(r, 0.0, 1.0));
  vec2 d = r < 1e-6 ? vec2(0.0, 1.0) : p / r;
  return vec3(sin(theta) * d.x, sin(theta) * d.y, cos(theta));
}

// Cube cross: the six faces unfolded flat, for inspecting a panorama's whole
// content at once without the pole stretching that equirectangular imposes.
vec3 dirCubeCross(vec2 p, out bool hit) {
  vec2 q = vec2((p.x * 0.5 + 0.5) * 4.0, (1.0 - (p.y * 0.5 + 0.5)) * 3.0);
  int cx = int(floor(q.x));
  int cy = int(floor(q.y));
  vec2 f = fract(q) * 2.0 - 1.0;
  hit = true;
  if (cy == 1) {
    if (cx == 0) return normalize(vec3(-1.0, -f.y, -f.x));   // -X
    if (cx == 1) return normalize(vec3(f.x, -f.y, 1.0));     // +Z  (front)
    if (cx == 2) return normalize(vec3(1.0, -f.y, -f.x));    // +X
    if (cx == 3) return normalize(vec3(-f.x, -f.y, -1.0));   // -Z
  } else if (cy == 0 && cx == 1) {
    return normalize(vec3(f.x, 1.0, f.y));                   // +Y (zenith)
  } else if (cy == 2 && cx == 1) {
    return normalize(vec3(f.x, -1.0, -f.y));                 // -Y (nadir)
  }
  hit = false;
  return vec3(0.0, 0.0, 1.0);
}

void main() {
  vec2 p = vUv * 2.0 - 1.0;
  p.x *= uAspect;

  vec3 dir;
  bool hit = true;
  bool isGlobe = false;
  float shade = 1.0;

  if (uMode == 0) {
    // Immersive: standing inside the sphere. Morph continuously toward
    // stereographic so "pull back into a little planet" is one smooth move.
    vec3 a = dirRectilinear(p, uFov);
    if (uMorph > 0.001) {
      vec3 b = dirStereographic(p, uFov);
      dir = normalize(mix(a, b, uMorph));
    } else {
      dir = a;
    }
  } else if (uMode == 1) {
    dir = dirStereographic(p, uFov);
  } else if (uMode == 2) {
    dir = dirEquirectFlat(p, uFov / PI);
  } else if (uMode == 3) {
    dir = dirPannini(p, uFov, uPanniniD);
    hit = length(dir) > 0.5;
  } else if (uMode == 4) {
    dir = dirMercator(p, uFov / PI);
  } else if (uMode == 5) {
    dir = dirMirrorBall(p, hit);
  } else if (uMode == 7) {
    // the cross is a fixed 4:3 arrangement, so letterbox it into the viewport
    // instead of stretching the faces with the window
    vec2 sc = vUv * 2.0 - 1.0;
    float box = 4.0 / 3.0;
    vec2 q = sc;
    if (uAspect > box) q.x = sc.x * uAspect / box;
    else               q.y = sc.y * box / uAspect;
    dir = dirCubeCross(q, hit);
  } else if (uMode == 6) {
    // Globe: the panorama as a solid ball seen from OUTSIDE. Same texture, opposite
    // side of the surface -- this is the literal inverse of the immersive view.
    vec3 ro = vec3(0.0, 0.0, -uGlobeDist);
    vec3 rd = dirRectilinear(p, uFov);
    float b = dot(ro, rd);
    float c = dot(ro, ro) - 1.0;
    float disc = b * b - c;
    if (disc < 0.0) {
      hit = false;
      dir = vec3(0.0, 0.0, 1.0);
    } else {
      float tHit = -b - sqrt(disc);
      vec3 pt = ro + rd * tHit;
      dir = normalize(pt);
      isGlobe = true;
      // a little diffuse shading so the ball reads as a three-dimensional object
      // rather than a flat disc of texture
      shade = 0.78 + 0.22 * clamp(dot(dir, normalize(vec3(-0.4, 0.5, -1.0))), 0.0, 1.0);
    }
  } else {
    dir = dirRectilinear(p, uFov);
  }

  if (!hit) {
    fragColor = vec4(uBackground, 1.0);
    return;
  }

  vec3 world = normalize(uCamRot * dir);
  if (isGlobe) {
    // the globe's surface point is already a world direction once rotated
    world = normalize(uCamRot * dir);
  }

  bool inside;
  vec4 texel = samplePano(world, inside);
  vec3 col = inside ? texel.rgb * uExposure * shade : uBackground;

  if (uVignette > 0.0) {
    float r = length(vUv * 2.0 - 1.0);
    col *= mix(1.0, smoothstep(1.6, 0.25, r), uVignette);
  }

  fragColor = vec4(col, 1.0);
}
`;

/** Human-facing catalogue of the modes the shader implements. */
export const MODES = [
  {
    id: 0, key: 'immersive', name: 'Immersive 360',
    blurb: 'Inside the sphere, looking out — the view the DJI album gives you.',
    needsSphere: false, defaultFov: 75, fovRange: [25, 140], morph: true,
  },
  {
    id: 6, key: 'globe', name: 'Globe',
    blurb: 'The panorama wrapped on a ball you orbit from outside.',
    needsSphere: true, defaultFov: 45, fovRange: [20, 90],
  },
  {
    id: 1, key: 'planet', name: 'Little Planet',
    blurb: 'Stereographic from below — the ground curls into a tiny world.',
    needsSphere: true, defaultFov: 205, fovRange: [100, 330], pitch: -90,
  },
  {
    id: 1, key: 'tunnel', name: 'Tunnel',
    blurb: 'Stereographic from above — the same trick inverted, looking up a shaft.',
    needsSphere: true, defaultFov: 205, fovRange: [100, 330], pitch: 90,
  },
  {
    id: 1, key: 'fisheye', name: 'Fisheye',
    blurb: 'Very wide but still natural — good for one big landmark.',
    needsSphere: false, defaultFov: 150, fovRange: [80, 220],
  },
  {
    id: 3, key: 'pannini', name: 'Pannini',
    blurb: 'Wide angle that keeps verticals upright. Best for towns and canyons.',
    needsSphere: false, defaultFov: 130, fovRange: [70, 170],
  },
  {
    id: 2, key: 'flat', name: 'Flat (equirect)',
    blurb: 'The raw 2:1 map. Pan and zoom to inspect the whole frame.',
    needsSphere: false, defaultFov: 180, fovRange: [40, 360],
  },
  {
    id: 4, key: 'mercator', name: 'Mercator',
    blurb: 'Straight verticals across a wide strip — a cartographer’s panorama.',
    needsSphere: false, defaultFov: 180, fovRange: [60, 320],
  },
  {
    id: 5, key: 'mirror', name: 'Mirror Ball',
    blurb: 'The entire sphere reflected in a chrome bead.',
    needsSphere: true, defaultFov: 90, fovRange: [90, 90],
  },
  {
    id: 7, key: 'cube', name: 'Cube Cross',
    blurb: 'Six faces unfolded — no pole stretching, good for checking the stitch.',
    needsSphere: true, defaultFov: 90, fovRange: [90, 90],
  },
];
