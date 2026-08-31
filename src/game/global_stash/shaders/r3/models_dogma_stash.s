-- Gbuffer matches models\model (bump). Glow is extra. Shadows like stock.

function normal		(shader, t_base, t_second, t_detail)
	shader:begin	("deffer_model_bump", "deffer_base_bump")
			: fog		(false)
			: emissive 	(true)
	shader:dx10texture	("s_base",	t_base)
	shader:dx10texture	("s_bump",	t_base .. "_bump")
	shader:dx10texture	("s_bumpX",	t_base .. "_bump#")
	shader:dx10sampler	("smp_base")
	shader:dx10sampler	("smp_linear")
	shader:dx10stencil	( 	true, cmp_func.always,
							255 , 127,
							stencil_op.keep, stencil_op.replace, stencil_op.keep)
	shader:dx10stencil_ref	(1)
end

function l_point	(shader, t_base, t_second, t_detail)
	shader:begin	("shadow_direct_model", "dumb")
			: fog		(false)
	shader:dx10texture	("s_base",	t_base)
	shader:dx10sampler	("smp_base")
	shader:dx10color_write_enable	(false, false, false, false)
end

function l_special	(shader, t_base, t_second, t_detail)
	shader:begin	("deffer_model_flat",	"accum_emissive_dogma")
			: zb 		(true,false)
			: fog		(false)
			: emissive 	(true)
			: blend		(true, blend.srcalpha, blend.one)

	shader:dx10texture	("s_base",	t_base)
	shader:dx10sampler	("smp_base")
end
