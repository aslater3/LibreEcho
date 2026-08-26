/*
LibreEcho Echo Gen 2 six-pogo rounded-rectangle plug, revision 5.
Optimised for slicing with a 0.4 mm FDM nozzle.

The previous 0.68-0.75 mm enclosed bores are below reliable FDM feature
size. This revision provides larger straight bores and deliberately enlarged,
tapered mouths on both print faces so first-layer squish cannot seal the bore.
The 0.66 mm pogo barrels should be retained with a very small amount of epoxy
or UV resin at the wiring-side pocket, kept away from the moving plunger.

CLI examples:
  openscad -D 'part="plug"' -D 'pin_bore_d=1.0' -o plug.stl this_file.scad
  openscad -D 'part="coupon"' -o coupon.stl this_file.scad
*/

$fn = 96;
part = "plug"; // plug, coupon, preview

// Corrected plug geometry from measurements.
lid_l = 10.0;
lid_w = 7.6;
lid_r = 2.0;
lid_t = 2.0;
body_l = 8.8;
body_w = 6.0;
body_r = 1.8;
insert_depth = 5.0;
lead_in_depth = 0.60;
lead_l = 8.15;
lead_w = 5.35;
lead_r = 1.50;
ridge_l = 9.7;
ridge_w = 6.9;
ridge_h = 0.56;
ridge_centres = [1.10, 2.20, 3.30, 4.35];

// 2 x 3 pin pattern.
pitch_x = 3.0;
pitch_y = 3.0;
pogo_barrel_d = 0.66;

// Print-safe bore. Recommended first full plug: 1.0 mm.
pin_bore_d = 1.0;
mouth_extra_d = 0.60;       // 1.60 mm mouth for a 1.00 mm bore.
mouth_depth = 0.70;
wire_pocket_extra_d = 0.80; // Slightly wider top pocket for adhesive.
wire_pocket_depth = 0.65;

// Visual orientation dimple.
key_dimple_d = 0.75;
key_dimple_depth = 0.40;
key_x = -4.10;
key_y = 0.00;

coupon_bores = [0.80, 0.90, 1.00, 1.10, 1.20];

function xpos(i) = (i - 1) * pitch_x;
function ypos(j) = (j - 0.5) * pitch_y;

module rr2d(l, w, r) {
    offset(r=r) square([l - 2*r, w - 2*r], center=true);
}

module rr_prism(l, w, r, h, z=0) {
    translate([0,0,z]) linear_extrude(height=h) rr2d(l,w,r);
}

module rr_loft(l0,w0,r0,z0,l1,w1,r1,z1) {
    hull() {
        translate([0,0,z0]) linear_extrude(height=0.01) rr2d(l0,w0,r0);
        translate([0,0,z1-0.01]) linear_extrude(height=0.01) rr2d(l1,w1,r1);
    }
}

module raw_plug() {
    union() {
        rr_loft(lead_l,lead_w,lead_r,0,
                body_l,body_w,body_r,lead_in_depth);
        rr_prism(body_l,body_w,body_r,insert_depth-lead_in_depth,lead_in_depth);

        for (zc=ridge_centres)
            if (zc-ridge_h/2 > 0 && zc+ridge_h/2 < insert_depth) {
                local_r = min(body_r+(ridge_l-body_l)/2,
                              body_r+(ridge_w-body_w)/2);
                rr_loft(body_l,body_w,body_r,zc-ridge_h/2,
                        ridge_l,ridge_w,local_r,zc);
                rr_loft(ridge_l,ridge_w,local_r,zc,
                        body_l,body_w,body_r,zc+ridge_h/2);
            }

        rr_prism(lid_l,lid_w,lid_r,lid_t,insert_depth);
    }
}

module print_safe_hole(x,y,bore=pin_bore_d,total_h=insert_depth+lid_t) {
    mouth_d = bore + mouth_extra_d;
    pocket_d = bore + wire_pocket_extra_d;

    // Straight through-channel.
    translate([x,y,-0.25]) cylinder(h=total_h+0.50,d=bore);

    // Contact-side flared mouth. This is the body end at z=0.
    translate([x,y,-0.01])
        cylinder(h=mouth_depth+0.02,d1=mouth_d,d2=bore);

    // Wiring-side adhesive pocket, at the outside face of the lid.
    translate([x,y,total_h-wire_pocket_depth])
        cylinder(h=wire_pocket_depth+0.02,d1=bore,d2=pocket_d);
}

module plug(bore=pin_bore_d) {
    total_h = insert_depth + lid_t;
    difference() {
        raw_plug();
        for (i=[0:2]) for (j=[0:1])
            print_safe_hole(xpos(i),ypos(j),bore,total_h);

        translate([key_x,key_y,total_h-key_dimple_depth])
            cylinder(h=key_dimple_depth+0.05,d=key_dimple_d);
    }
}

// Five-hole flared-bore coupon, left to right: 0.8, 0.9, 1.0, 1.1, 1.2 mm.
module coupon() {
    coupon_l = 34.0;
    coupon_w = 10.0;
    coupon_t = 3.2;
    xvals = [-12,-6,0,6,12];
    difference() {
        rr_prism(coupon_l,coupon_w,1.5,coupon_t,0);
        for (k=[0:4]) {
            bore = coupon_bores[k];
            x = xvals[k];
            // Through bore plus 0.6 mm flare at both faces.
            translate([x,0,-0.2]) cylinder(h=coupon_t+0.4,d=bore);
            translate([x,0,-0.01])
                cylinder(h=0.62,d1=bore+0.60,d2=bore);
            translate([x,0,coupon_t-0.60])
                cylinder(h=0.62,d1=bore,d2=bore+0.60);
        }
    }
    // Raised labels are intentionally large enough for a 0.4 mm nozzle.
    labels = ["0.8","0.9","1.0","1.1","1.2"];
    for (k=[0:4])
        translate([xvals[k],-3.1,coupon_t])
            linear_extrude(height=0.45)
                text(labels[k],size=1.65,halign="center",valign="center",
                     font="Liberation Sans:style=Bold");
}

module pogo_visual(x,y) {
    color([0.78,0.57,0.12]) {
        translate([x,y,-2.0]) cylinder(h=9.5,d=pogo_barrel_d);
        translate([x,y,-2.0]) sphere(d=0.72);
        translate([x,y,7.4]) cylinder(h=5.0,d=0.48);
    }
}

module preview() {
    color([0.12,0.37,0.56]) plug(pin_bore_d);
    for (i=[0:2]) for (j=[0:1]) pogo_visual(xpos(i),ypos(j));
}

if (part == "plug") plug(pin_bore_d);
else if (part == "coupon") coupon();
else if (part == "preview") preview();
else assert(false,str("Unknown part: ",part));
