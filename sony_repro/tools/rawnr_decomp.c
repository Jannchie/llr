// 7 functions, roots=0x384cb0 0x39fab0 0x3b4bb0 0x385af0 0x3857e0 0x39d230 0x39d050 depth=0

// ===== depth0 FUN_140384cb0 @ 0x140384cb0 rva=0x384cb0 size=2850 =====

/* WARNING: Function: __chkstk replaced with injection: alloca_probe */
/* WARNING: Function: __security_check_cookie replaced with injection: security_check_cookie */
/* WARNING: Globals starting with '_' overlap smaller symbols at the same address */

int FUN_140384cb0(undefined8 param_1,longlong param_2,undefined8 *param_3)

{
  undefined4 uVar1;
  uint uVar2;
  uint uVar3;
  int iVar4;
  int iVar5;
  int iVar6;
  int iVar7;
  void *pvVar8;
  undefined8 *puVar9;
  longlong *plVar10;
  longlong lVar11;
  longlong lVar12;
  undefined8 uVar13;
  int iVar14;
  undefined8 *puVar15;
  int iVar16;
  int iVar17;
  int iVar18;
  int iVar19;
  undefined8 *puVar20;
  undefined *puVar21;
  uint uVar22;
  uint uVar24;
  longlong lVar23;
  uint uVar25;
  uint uVar29;
  undefined1 auVar26 [16];
  undefined1 auVar27 [16];
  undefined1 auVar28 [16];
  undefined1 auVar30 [16];
  undefined1 auVar31 [16];
  undefined1 auVar32 [16];
  undefined1 auVar33 [16];
  undefined1 auVar34 [16];
  undefined1 auVar35 [16];
  undefined1 auVar36 [16];
  undefined1 auVar37 [16];
  undefined1 auVar38 [16];
  undefined **ppuStack_20128;
  undefined4 uStack_20120;
  undefined4 uStack_2011c;
  undefined4 uStack_20118;
  undefined4 uStack_20114;
  undefined8 uStack_20110;
  undefined8 uStack_20108;
  undefined8 uStack_20100;
  undefined8 uStack_200f8;
  undefined8 uStack_200f0;
  undefined8 uStack_200e8;
  undefined8 uStack_200e0;
  ulonglong auStack_200d8 [16404];
  
  iVar6 = 1;
  if (((param_2 != 0) && (param_3 != (undefined8 *)0x0)) &&
     (lVar23 = *(longlong *)(param_2 + 8), lVar23 != 0)) {
    ppuStack_20128 = sony_zhacai::ZcRectT<int>::vftable;
    uStack_20120 = *(undefined4 *)(param_2 + 0x30);
    uStack_2011c = *(undefined4 *)(param_2 + 0x34);
    uStack_20118 = *(undefined4 *)(param_2 + 0x38);
    uStack_20114 = *(undefined4 *)(param_2 + 0x3c);
    pvVar8 = operator_new(0x30);
    puVar20 = (undefined8 *)0x0;
    iVar19 = 0;
    puVar9 = puVar20;
    if (pvVar8 != (void *)0x0) {
      puVar9 = (undefined8 *)FUN_140152660(pvVar8);
    }
    if (puVar9 != (undefined8 *)0x0) {
      iVar6 = FUN_1401529d0(puVar9,lVar23);
      if (iVar6 == 0) {
        plVar10 = (longlong *)
                  __RTDynamicCast(*param_3,0,&sony_zhacai::IZcImage::RTTI_Type_Descriptor,
                                  &sony_zhacai::ZcARW::RTTI_Type_Descriptor);
        lVar11 = (**(code **)(*plVar10 + 0xd8))(plVar10,*(undefined4 *)(param_3[1] + 0x1e0));
        uStack_20110 = 0;
        uStack_20108 = 0;
        uStack_20100 = 0;
        uStack_200f8 = 0;
        uStack_200f0 = 0;
        uStack_200e8 = 0;
        uStack_200e0 = 0;
        FUN_140152c00(&uStack_20110);
        lVar12 = FUN_140152710(puVar9,0);
        uVar1 = *(undefined4 *)(lVar12 + 0xc);
        lVar12 = FUN_140152710(puVar9,0);
        iVar6 = FUN_140152cb0(&uStack_20110,*(undefined4 *)(lVar12 + 8),uVar1);
        if (iVar6 == 0) {
          (*(code *)ppuStack_20128[0xb])(&ppuStack_20128,1,1,1);
          uVar13 = FUN_140152710(lVar23,0);
          FUN_140385af0(param_1,uVar13,&uStack_20110,&ppuStack_20128);
          (*(code *)ppuStack_20128[0xb])(&ppuStack_20128,1,1,1);
          iVar17 = *(int *)(lVar11 + 0x103c) * *(int *)(lVar11 + 0x1034) * 3 >> 8;
          iVar18 = *(int *)(lVar11 + 0x103c) * *(int *)(lVar11 + 0x1038) * 3 >> 8;
          if (DAT_140559490 < 2) {
            iVar4 = *(int *)(lVar11 + 0x1030);
            iVar5 = *(int *)(lVar11 + 0x102c);
            puVar15 = puVar20;
            do {
              iVar16 = (int)puVar15;
              iVar7 = iVar5;
              if (iVar5 <= iVar16) {
                iVar7 = iVar16;
              }
              iVar14 = iVar4;
              if (iVar16 <= iVar4) {
                iVar14 = iVar7;
              }
              iVar7 = ((iVar14 - iVar5) * iVar18 >> 0xc) + iVar17;
              iVar16 = iVar19;
              if (-1 < iVar7) {
                iVar16 = iVar7;
              }
              if (0x3fff < iVar7) {
                iVar16 = 0x3fff;
              }
              *(int *)((longlong)auStack_200d8 + (longlong)puVar15 * 4) = iVar16;
              puVar15 = (undefined8 *)((longlong)puVar15 + 1);
            } while (puVar15 < (undefined8 *)0x8000);
          }
          else {
            uVar2 = *(uint *)(lVar11 + 0x1030);
            uVar3 = *(uint *)(lVar11 + 0x102c);
            puVar15 = puVar20;
            do {
              puVar21 = (undefined *)((longlong)puVar15 + (longlong)PTR_140468aa0);
              lVar12 = (longlong)puVar15 + _UNK_140468aa8;
              uVar25 = (uint)puVar21;
              uVar29 = (uint)((ulonglong)puVar21 >> 0x20);
              uVar22 = (uint)lVar12;
              uVar24 = (uint)((ulonglong)lVar12 >> 0x20);
              auVar33._8_8_ = 0;
              auVar33._0_8_ = CONCAT44(iVar18,iVar18);
              auVar26._8_4_ = ~-(uint)(0 < (int)uVar29) & ~-(uint)((longlong)puVar21 < 0) & uVar29;
              auVar26._12_4_ = ~-(uint)(0 < (int)uVar24) & ~-(uint)(lVar12 < 0) & uVar24;
              auVar26._0_4_ =
                   (~-(uint)((int)uVar2 < (int)uVar25) &
                    (uVar3 & -(uint)((int)uVar25 < (int)uVar3) |
                    ~-(uint)((int)uVar25 < (int)uVar3) & uVar25) |
                   uVar2 & -(uint)((int)uVar2 < (int)uVar25)) - uVar3;
              auVar26._4_4_ =
                   (~-(uint)((int)uVar2 < (int)uVar22) &
                    (uVar3 & -(uint)((int)uVar22 < (int)uVar3) |
                    ~-(uint)((int)uVar22 < (int)uVar3) & uVar22) |
                   uVar2 & -(uint)((int)uVar2 < (int)uVar22)) - uVar3;
              auVar26 = pmulld(auVar26,auVar33);
              uVar25 = (auVar26._0_4_ >> 0xc) + iVar17;
              uVar29 = (auVar26._4_4_ >> 0xc) + iVar17;
              *(ulonglong *)((longlong)auStack_200d8 + (longlong)puVar15 * 4) =
                   CONCAT44(~-(uint)((int)_UNK_140468ab4 < (int)uVar29) &
                            ~-(uint)((int)uVar29 < 0) & uVar29,
                            ~-(uint)((int)_DAT_140468ab0 < (int)uVar25) &
                            ~-(uint)((int)uVar25 < 0) & uVar25) |
                   CONCAT44(_UNK_140468ab4 & -(uint)((int)_UNK_140468ab4 < (int)uVar29),
                            _DAT_140468ab0 & -(uint)((int)_DAT_140468ab0 < (int)uVar25));
              puVar21 = PTR_140468aa0 + (longlong)puVar15 + 2;
              lVar12 = (longlong)puVar15 + 2 + _UNK_140468aa8;
              uVar25 = (uint)puVar21;
              uVar29 = (uint)((ulonglong)puVar21 >> 0x20);
              uVar22 = (uint)lVar12;
              uVar24 = (uint)((ulonglong)lVar12 >> 0x20);
              auVar36._8_8_ = 0;
              auVar36._0_8_ = CONCAT44(iVar18,iVar18);
              auVar30._8_4_ = ~-(uint)(0 < (int)uVar29) & ~-(uint)((longlong)puVar21 < 0) & uVar29;
              auVar30._12_4_ = ~-(uint)(0 < (int)uVar24) & ~-(uint)(lVar12 < 0) & uVar24;
              auVar30._0_4_ =
                   (~-(uint)((int)uVar2 < (int)uVar25) &
                    (~-(uint)((int)uVar25 < (int)uVar3) & uVar25 |
                    uVar3 & -(uint)((int)uVar25 < (int)uVar3)) |
                   uVar2 & -(uint)((int)uVar2 < (int)uVar25)) - uVar3;
              auVar30._4_4_ =
                   (~-(uint)((int)uVar2 < (int)uVar22) &
                    (~-(uint)((int)uVar22 < (int)uVar3) & uVar22 |
                    uVar3 & -(uint)((int)uVar22 < (int)uVar3)) |
                   uVar2 & -(uint)((int)uVar2 < (int)uVar22)) - uVar3;
              auVar26 = pmulld(auVar30,auVar36);
              uVar25 = (auVar26._0_4_ >> 0xc) + iVar17;
              uVar29 = (auVar26._4_4_ >> 0xc) + iVar17;
              *(ulonglong *)((longlong)auStack_200d8 + (longlong)puVar15 * 4 + 8) =
                   CONCAT44(~-(uint)((int)_UNK_140468ab4 < (int)uVar29) &
                            ~-(uint)((int)uVar29 < 0) & uVar29,
                            ~-(uint)((int)_DAT_140468ab0 < (int)uVar25) &
                            ~-(uint)((int)uVar25 < 0) & uVar25) |
                   CONCAT44(_UNK_140468ab4 & -(uint)((int)_UNK_140468ab4 < (int)uVar29),
                            _DAT_140468ab0 & -(uint)((int)_DAT_140468ab0 < (int)uVar25));
              puVar15 = (undefined8 *)((longlong)puVar15 + 4);
            } while (puVar15 < (undefined8 *)0x8000);
          }
          uVar13 = FUN_140152710(puVar9,0);
          FUN_1403857e0(param_1,&uStack_20110,uVar13,&ppuStack_20128);
          (*(code *)ppuStack_20128[8])(&ppuStack_20128,1,1,1);
          uVar13 = FUN_140152710(lVar23,1);
          FUN_140385af0(param_1,uVar13,&uStack_20110,&ppuStack_20128);
          (*(code *)ppuStack_20128[0xb])(&ppuStack_20128,1,1,1);
          iVar17 = *(int *)(lVar11 + 0x1040) * *(int *)(lVar11 + 0x1034) * 3 >> 8;
          iVar18 = *(int *)(lVar11 + 0x1040) * *(int *)(lVar11 + 0x1038) * 3 >> 8;
          if (DAT_140559490 < 2) {
            iVar4 = *(int *)(lVar11 + 0x1030);
            iVar5 = *(int *)(lVar11 + 0x102c);
            puVar15 = puVar20;
            do {
              iVar16 = (int)puVar15;
              iVar7 = iVar5;
              if (iVar5 <= iVar16) {
                iVar7 = iVar16;
              }
              iVar14 = iVar4;
              if (iVar16 <= iVar4) {
                iVar14 = iVar7;
              }
              iVar7 = ((iVar14 - iVar5) * iVar18 >> 0xc) + iVar17;
              iVar16 = iVar19;
              if (-1 < iVar7) {
                iVar16 = iVar7;
              }
              if (0x3fff < iVar7) {
                iVar16 = 0x3fff;
              }
              *(int *)((longlong)auStack_200d8 + (longlong)puVar15 * 4) = iVar16;
              puVar15 = (undefined8 *)((longlong)puVar15 + 1);
            } while (puVar15 < (undefined8 *)0x8000);
          }
          else {
            uVar2 = *(uint *)(lVar11 + 0x1030);
            uVar3 = *(uint *)(lVar11 + 0x102c);
            puVar15 = puVar20;
            do {
              puVar21 = (undefined *)((longlong)puVar15 + (longlong)PTR_140468aa0);
              lVar12 = (longlong)puVar15 + _UNK_140468aa8;
              uVar25 = (uint)puVar21;
              uVar29 = (uint)((ulonglong)puVar21 >> 0x20);
              uVar22 = (uint)lVar12;
              uVar24 = (uint)((ulonglong)lVar12 >> 0x20);
              auVar34._8_8_ = 0;
              auVar34._0_8_ = CONCAT44(iVar18,iVar18);
              auVar27._8_4_ = ~-(uint)(0 < (int)uVar29) & ~-(uint)((longlong)puVar21 < 0) & uVar29;
              auVar27._12_4_ = ~-(uint)(0 < (int)uVar24) & ~-(uint)(lVar12 < 0) & uVar24;
              auVar27._0_4_ =
                   (~-(uint)((int)uVar2 < (int)uVar25) &
                    (uVar3 & -(uint)((int)uVar25 < (int)uVar3) |
                    ~-(uint)((int)uVar25 < (int)uVar3) & uVar25) |
                   uVar2 & -(uint)((int)uVar2 < (int)uVar25)) - uVar3;
              auVar27._4_4_ =
                   (~-(uint)((int)uVar2 < (int)uVar22) &
                    (uVar3 & -(uint)((int)uVar22 < (int)uVar3) |
                    ~-(uint)((int)uVar22 < (int)uVar3) & uVar22) |
                   uVar2 & -(uint)((int)uVar2 < (int)uVar22)) - uVar3;
              auVar26 = pmulld(auVar27,auVar34);
              uVar25 = (auVar26._0_4_ >> 0xc) + iVar17;
              uVar29 = (auVar26._4_4_ >> 0xc) + iVar17;
              *(ulonglong *)((longlong)auStack_200d8 + (longlong)puVar15 * 4) =
                   CONCAT44(~-(uint)((int)_UNK_140468ab4 < (int)uVar29) &
                            ~-(uint)((int)uVar29 < 0) & uVar29,
                            ~-(uint)((int)_DAT_140468ab0 < (int)uVar25) &
                            ~-(uint)((int)uVar25 < 0) & uVar25) |
                   CONCAT44(_UNK_140468ab4 & -(uint)((int)_UNK_140468ab4 < (int)uVar29),
                            _DAT_140468ab0 & -(uint)((int)_DAT_140468ab0 < (int)uVar25));
              puVar21 = PTR_140468aa0 + (longlong)puVar15 + 2;
              lVar12 = (longlong)puVar15 + 2 + _UNK_140468aa8;
              uVar25 = (uint)puVar21;
              uVar29 = (uint)((ulonglong)puVar21 >> 0x20);
              uVar22 = (uint)lVar12;
              uVar24 = (uint)((ulonglong)lVar12 >> 0x20);
              auVar35._8_8_ = 0;
              auVar35._0_8_ = CONCAT44(iVar18,iVar18);
              auVar28._8_4_ = ~-(uint)(0 < (int)uVar29) & ~-(uint)((longlong)puVar21 < 0) & uVar29;
              auVar28._12_4_ = ~-(uint)(0 < (int)uVar24) & ~-(uint)(lVar12 < 0) & uVar24;
              auVar28._0_4_ =
                   (~-(uint)((int)uVar2 < (int)uVar25) &
                    (uVar3 & -(uint)((int)uVar25 < (int)uVar3) |
                    ~-(uint)((int)uVar25 < (int)uVar3) & uVar25) |
                   uVar2 & -(uint)((int)uVar2 < (int)uVar25)) - uVar3;
              auVar28._4_4_ =
                   (~-(uint)((int)uVar2 < (int)uVar22) &
                    (uVar3 & -(uint)((int)uVar22 < (int)uVar3) |
                    ~-(uint)((int)uVar22 < (int)uVar3) & uVar22) |
                   uVar2 & -(uint)((int)uVar2 < (int)uVar22)) - uVar3;
              auVar26 = pmulld(auVar28,auVar35);
              uVar25 = (auVar26._0_4_ >> 0xc) + iVar17;
              uVar29 = (auVar26._4_4_ >> 0xc) + iVar17;
              *(ulonglong *)((longlong)auStack_200d8 + (longlong)puVar15 * 4 + 8) =
                   CONCAT44(~-(uint)((int)_UNK_140468ab4 < (int)uVar29) &
                            ~-(uint)((int)uVar29 < 0) & uVar29,
                            ~-(uint)((int)_DAT_140468ab0 < (int)uVar25) &
                            ~-(uint)((int)uVar25 < 0) & uVar25) |
                   CONCAT44(_UNK_140468ab4 & -(uint)((int)_UNK_140468ab4 < (int)uVar29),
                            _DAT_140468ab0 & -(uint)((int)_DAT_140468ab0 < (int)uVar25));
              puVar15 = (undefined8 *)((longlong)puVar15 + 4);
            } while (puVar15 < (undefined8 *)0x8000);
          }
          uVar13 = FUN_140152710(puVar9,1);
          FUN_1403857e0(param_1,&uStack_20110,uVar13,&ppuStack_20128);
          (*(code *)ppuStack_20128[8])(&ppuStack_20128,1,1,1);
          uVar13 = FUN_140152710(lVar23,2);
          FUN_140385af0(param_1,uVar13,&uStack_20110,&ppuStack_20128);
          (*(code *)ppuStack_20128[0xb])(&ppuStack_20128,1,1,1);
          iVar17 = *(int *)(lVar11 + 0x1044) * *(int *)(lVar11 + 0x1034) * 3 >> 8;
          iVar18 = *(int *)(lVar11 + 0x1044) * *(int *)(lVar11 + 0x1038) * 3 >> 8;
          if (DAT_140559490 < 2) {
            iVar4 = *(int *)(lVar11 + 0x1030);
            iVar5 = *(int *)(lVar11 + 0x102c);
            do {
              iVar16 = (int)puVar20;
              iVar7 = iVar5;
              if (iVar5 <= iVar16) {
                iVar7 = iVar16;
              }
              iVar14 = iVar4;
              if (iVar16 <= iVar4) {
                iVar14 = iVar7;
              }
              iVar7 = ((iVar14 - iVar5) * iVar18 >> 0xc) + iVar17;
              iVar16 = iVar19;
              if (-1 < iVar7) {
                iVar16 = iVar7;
              }
              if (0x3fff < iVar7) {
                iVar16 = 0x3fff;
              }
              *(int *)((longlong)auStack_200d8 + (longlong)puVar20 * 4) = iVar16;
              puVar20 = (undefined8 *)((longlong)puVar20 + 1);
            } while (puVar20 < (undefined8 *)0x8000);
          }
          else {
            uVar2 = *(uint *)(lVar11 + 0x1030);
            uVar3 = *(uint *)(lVar11 + 0x102c);
            do {
              puVar21 = (undefined *)((longlong)puVar20 + (longlong)PTR_140468aa0);
              lVar23 = (longlong)puVar20 + _UNK_140468aa8;
              uVar25 = (uint)puVar21;
              uVar29 = (uint)((ulonglong)puVar21 >> 0x20);
              uVar22 = (uint)lVar23;
              uVar24 = (uint)((ulonglong)lVar23 >> 0x20);
              auVar37._8_8_ = 0;
              auVar37._0_8_ = CONCAT44(iVar18,iVar18);
              auVar31._8_4_ = ~-(uint)(0 < (int)uVar29) & ~-(uint)((longlong)puVar21 < 0) & uVar29;
              auVar31._12_4_ = ~-(uint)(0 < (int)uVar24) & ~-(uint)(lVar23 < 0) & uVar24;
              auVar31._0_4_ =
                   (~-(uint)((int)uVar2 < (int)uVar25) &
                    (-(uint)((int)uVar25 < (int)uVar3) & uVar3 |
                    ~-(uint)((int)uVar25 < (int)uVar3) & uVar25) |
                   -(uint)((int)uVar2 < (int)uVar25) & uVar2) - uVar3;
              auVar31._4_4_ =
                   (~-(uint)((int)uVar2 < (int)uVar22) &
                    (-(uint)((int)uVar22 < (int)uVar3) & uVar3 |
                    ~-(uint)((int)uVar22 < (int)uVar3) & uVar22) |
                   -(uint)((int)uVar2 < (int)uVar22) & uVar2) - uVar3;
              auVar26 = pmulld(auVar31,auVar37);
              uVar25 = (auVar26._0_4_ >> 0xc) + iVar17;
              uVar29 = (auVar26._4_4_ >> 0xc) + iVar17;
              *(ulonglong *)((longlong)auStack_200d8 + (longlong)puVar20 * 4) =
                   CONCAT44(~-(uint)((int)_UNK_140468ab4 < (int)uVar29) &
                            ~-(uint)((int)uVar29 < 0) & uVar29,
                            ~-(uint)((int)_DAT_140468ab0 < (int)uVar25) &
                            ~-(uint)((int)uVar25 < 0) & uVar25) |
                   CONCAT44(_UNK_140468ab4 & -(uint)((int)_UNK_140468ab4 < (int)uVar29),
                            _DAT_140468ab0 & -(uint)((int)_DAT_140468ab0 < (int)uVar25));
              puVar21 = PTR_140468aa0 + (longlong)puVar20 + 2;
              lVar23 = (longlong)puVar20 + 2 + _UNK_140468aa8;
              uVar25 = (uint)puVar21;
              uVar29 = (uint)((ulonglong)puVar21 >> 0x20);
              uVar22 = (uint)lVar23;
              uVar24 = (uint)((ulonglong)lVar23 >> 0x20);
              auVar38._8_8_ = 0;
              auVar38._0_8_ = CONCAT44(iVar18,iVar18);
              auVar32._8_4_ = ~-(uint)(0 < (int)uVar29) & ~-(uint)((longlong)puVar21 < 0) & uVar29;
              auVar32._12_4_ = ~-(uint)(0 < (int)uVar24) & ~-(uint)(lVar23 < 0) & uVar24;
              auVar32._0_4_ =
                   (~-(uint)((int)uVar2 < (int)uVar25) &
                    (-(uint)((int)uVar25 < (int)uVar3) & uVar3 |
                    ~-(uint)((int)uVar25 < (int)uVar3) & uVar25) |
                   -(uint)((int)uVar2 < (int)uVar25) & uVar2) - uVar3;
              auVar32._4_4_ =
                   (~-(uint)((int)uVar2 < (int)uVar22) &
                    (-(uint)((int)uVar22 < (int)uVar3) & uVar3 |
                    ~-(uint)((int)uVar22 < (int)uVar3) & uVar22) |
                   -(uint)((int)uVar2 < (int)uVar22) & uVar2) - uVar3;
              auVar26 = pmulld(auVar32,auVar38);
              uVar25 = (auVar26._0_4_ >> 0xc) + iVar17;
              uVar29 = (auVar26._4_4_ >> 0xc) + iVar17;
              *(ulonglong *)((longlong)auStack_200d8 + (longlong)puVar20 * 4 + 8) =
                   CONCAT44(~-(uint)((int)_UNK_140468ab4 < (int)uVar29) &
                            ~-(uint)((int)uVar29 < 0) & uVar29,
                            ~-(uint)((int)_DAT_140468ab0 < (int)uVar25) &
                            ~-(uint)((int)uVar25 < 0) & uVar25) |
                   CONCAT44(_UNK_140468ab4 & -(uint)((int)_UNK_140468ab4 < (int)uVar29),
                            _DAT_140468ab0 & -(uint)((int)_DAT_140468ab0 < (int)uVar25));
              puVar20 = (undefined8 *)((longlong)puVar20 + 4);
            } while (puVar20 < (undefined8 *)0x8000);
          }
          uVar13 = FUN_140152710(puVar9,2);
          FUN_1403857e0(param_1,&uStack_20110,uVar13,&ppuStack_20128);
          FUN_14016b640(param_2,puVar9,0);
        }
        else {
          (**(code **)*puVar9)(puVar9);
        }
        FUN_140152c90(&uStack_20110);
      }
      else {
        (**(code **)*puVar9)(puVar9,1);
      }
    }
  }
  return iVar6;
}



// ===== depth0 FUN_14039fab0 @ 0x14039fab0 rva=0x39fab0 size=4324 =====

/* WARNING: Function: __security_check_cookie replaced with injection: security_check_cookie */

undefined8 FUN_14039fab0(undefined8 param_1,longlong param_2,longlong *param_3)

{
  undefined1 auVar1 [12];
  undefined1 auVar2 [12];
  int iVar3;
  longlong *plVar4;
  longlong lVar5;
  longlong lVar6;
  undefined1 (*pauVar7) [16];
  undefined8 uVar8;
  float *pfVar9;
  float *pfVar10;
  float *pfVar11;
  float *pfVar12;
  uint uVar13;
  int iVar14;
  undefined1 (*pauVar15) [16];
  uint uVar16;
  ushort *puVar17;
  undefined2 *puVar18;
  float *pfVar19;
  float *pfVar20;
  uint uVar21;
  longlong lVar22;
  float *pfVar23;
  float *pfVar24;
  undefined1 (*pauVar25) [16];
  ulonglong uVar26;
  undefined1 (*pauVar27) [16];
  undefined1 (*pauVar28) [16];
  float fVar29;
  float fVar30;
  float fVar31;
  undefined1 auStack_198 [32];
  ulonglong local_178;
  longlong local_170;
  undefined4 local_168;
  int local_160;
  int local_158;
  undefined4 local_150;
  undefined4 local_148;
  int local_138;
  uint local_134;
  undefined1 (*local_130) [16];
  undefined1 (*local_128) [16];
  undefined1 (*local_120) [16];
  longlong local_118;
  undefined1 (*local_110) [16];
  undefined1 (*local_108) [16];
  undefined1 (*local_100) [16];
  undefined1 (*local_f8) [16];
  undefined1 (*local_f0) [16];
  undefined1 (*local_e8) [16];
  undefined1 (*local_e0) [16];
  undefined1 (*local_d8) [16];
  undefined1 (*local_d0) [16];
  undefined1 (*local_c8) [16];
  undefined1 (*local_c0) [16];
  uint local_b8;
  int local_b4;
  int local_b0;
  undefined1 (*local_a8) [16];
  undefined1 (*local_a0) [16];
  longlong local_98;
  undefined **local_90;
  uint local_88;
  uint local_84;
  uint local_80;
  int local_7c;
  ulonglong local_78;
  
  local_78 = DAT_140559440 ^ (ulonglong)auStack_198;
  pauVar25 = (undefined1 (*) [16])0x0;
  local_178 = local_178 & 0xffffffff00000000;
  local_118 = param_2;
  plVar4 = (longlong *)
           __RTDynamicCast(*param_3,0,&sony_zhacai::IZcImage::RTTI_Type_Descriptor,
                           &sony_zhacai::ZcARW::RTTI_Type_Descriptor);
  lVar5 = (**(code **)(*plVar4 + 0xd8))(plVar4,*(undefined4 *)(param_3[1] + 0x1e0));
  fVar29 = DAT_1404deb28;
  fVar31 = DAT_1404debbc;
  if ((longlong *)*param_3 != (longlong *)0x0) {
    uVar16 = (**(code **)(*(longlong *)*param_3 + 0xe0))();
    fVar29 = DAT_140559904;
    if ((DAT_140559900 <= uVar16) && (fVar29 = DAT_14055990c, uVar16 < DAT_140559908)) {
      fVar29 = ((float)uVar16 - (float)DAT_140559900) /
               ((float)DAT_140559908 - (float)DAT_140559900);
      fVar29 = (fVar31 - fVar29) * DAT_140559904 + fVar29 * DAT_14055990c;
    }
  }
  lVar6 = FUN_140152710(*(undefined8 *)(param_2 + 8),0);
  local_a0 = operator_new(0x30);
  pauVar7 = pauVar25;
  if (local_a0 != (void *)0x0) {
    pauVar7 = (undefined1 (*) [16])FUN_140152660(local_a0);
  }
  local_170._0_4_ = 0;
  local_178 = local_178 & 0xffffffffffffff00;
  local_a0 = pauVar7;
  FUN_1401527f0(pauVar7,0,*(undefined4 *)(lVar6 + 8));
  local_98 = FUN_140152710(pauVar7,0);
  local_90 = sony_zhacai::ZcRectT<int>::vftable;
  local_88 = *(uint *)(param_2 + 0x30);
  local_84 = *(uint *)(param_2 + 0x34);
  local_80 = *(uint *)(param_2 + 0x38);
  local_7c = *(int *)(param_2 + 0x3c);
  uVar16 = local_80 - local_88 >> 1;
  uVar13 = local_7c - local_84 >> 1;
  local_134 = uVar13;
  local_b8 = uVar16;
  local_a8 = operator_new(0x18);
  if (local_a8 == (undefined1 (*) [16])0x0) {
    local_a8 = (undefined1 (*) [16])0x0;
  }
  else {
    *local_a8 = (undefined1  [16])0x0;
    *(undefined8 *)*local_a8 = 0;
    *(undefined8 *)(*local_a8 + 8) = 0;
    *(undefined8 *)local_a8[1] = 0;
  }
  pauVar7 = local_a8;
  FUN_1403a0ba0(local_a8,uVar16,uVar13);
  local_c0 = operator_new(0x18);
  if (local_c0 != (undefined1 (*) [16])0x0) {
    *local_c0 = (undefined1  [16])0x0;
    *(undefined8 *)*local_c0 = 0;
    *(undefined8 *)(*local_c0 + 8) = 0;
    *(undefined8 *)local_c0[1] = 0;
    pauVar25 = local_c0;
  }
  FUN_1403a0ba0(pauVar25,uVar16,uVar13);
  local_c0 = operator_new(0x18);
  if (local_c0 == (undefined1 (*) [16])0x0) {
    local_c0 = (undefined1 (*) [16])0x0;
  }
  else {
    *local_c0 = (undefined1  [16])0x0;
    *(undefined8 *)*local_c0 = 0;
    *(undefined8 *)(*local_c0 + 8) = 0;
    *(undefined8 *)local_c0[1] = 0;
  }
  pauVar27 = local_c0;
  FUN_1403a0ba0(local_c0,uVar16,uVar13);
  local_130 = operator_new(0x18);
  if (local_130 != (undefined1 (*) [16])0x0) {
    *local_130 = (undefined1  [16])0x0;
    *(undefined8 *)*local_130 = 0;
    *(undefined8 *)(*local_130 + 8) = 0;
    *(undefined8 *)local_130[1] = 0;
  }
  FUN_1403a0ba0(local_130,uVar16,uVar13);
  local_108 = operator_new(0x18);
  if (local_108 != (undefined1 (*) [16])0x0) {
    *local_108 = (undefined1  [16])0x0;
    *(undefined8 *)*local_108 = 0;
    *(undefined8 *)(*local_108 + 8) = 0;
    *(undefined8 *)local_108[1] = 0;
  }
  FUN_1403a0ba0(local_108,uVar16,uVar13);
  local_100 = operator_new(0x18);
  if (local_100 != (undefined1 (*) [16])0x0) {
    *local_100 = (undefined1  [16])0x0;
    *(undefined8 *)*local_100 = 0;
    *(undefined8 *)(*local_100 + 8) = 0;
    *(undefined8 *)local_100[1] = 0;
  }
  FUN_1403a0ba0(local_100,uVar16,uVar13);
  local_f8 = operator_new(0x18);
  if (local_f8 != (undefined1 (*) [16])0x0) {
    *local_f8 = (undefined1  [16])0x0;
    *(undefined8 *)*local_f8 = 0;
    *(undefined8 *)(*local_f8 + 8) = 0;
    *(undefined8 *)local_f8[1] = 0;
  }
  FUN_1403a0ba0(local_f8,uVar16,uVar13);
  local_110 = operator_new(0x18);
  if (local_110 != (undefined1 (*) [16])0x0) {
    *local_110 = (undefined1  [16])0x0;
    *(undefined8 *)*local_110 = 0;
    *(undefined8 *)(*local_110 + 8) = 0;
    *(undefined8 *)local_110[1] = 0;
  }
  FUN_1403a0ba0(local_110,uVar16,uVar13);
  local_f0 = operator_new(0x18);
  if (local_f0 != (undefined1 (*) [16])0x0) {
    *local_f0 = (undefined1  [16])0x0;
    *(undefined8 *)*local_f0 = 0;
    *(undefined8 *)(*local_f0 + 8) = 0;
    *(undefined8 *)local_f0[1] = 0;
  }
  FUN_1403a0ba0(local_f0,uVar16,uVar13);
  local_e8 = operator_new(0x18);
  if (local_e8 != (undefined1 (*) [16])0x0) {
    *local_e8 = (undefined1  [16])0x0;
    *(undefined8 *)*local_e8 = 0;
    *(undefined8 *)(*local_e8 + 8) = 0;
    *(undefined8 *)local_e8[1] = 0;
  }
  FUN_1403a0ba0(local_e8,uVar16,uVar13);
  local_e0 = operator_new(0x18);
  if (local_e0 != (undefined1 (*) [16])0x0) {
    *local_e0 = (undefined1  [16])0x0;
    *(undefined8 *)*local_e0 = 0;
    *(undefined8 *)(*local_e0 + 8) = 0;
    *(undefined8 *)local_e0[1] = 0;
  }
  FUN_1403a0ba0(local_e0,uVar16,uVar13);
  local_128 = operator_new(0x18);
  if (local_128 != (undefined1 (*) [16])0x0) {
    *local_128 = (undefined1  [16])0x0;
    *(undefined8 *)*local_128 = 0;
    *(undefined8 *)(*local_128 + 8) = 0;
    *(undefined8 *)local_128[1] = 0;
  }
  FUN_1403a0ba0(local_128,uVar16,uVar13);
  local_d8 = operator_new(0x18);
  if (local_d8 != (undefined1 (*) [16])0x0) {
    *local_d8 = (undefined1  [16])0x0;
    *(undefined8 *)*local_d8 = 0;
    *(undefined8 *)(*local_d8 + 8) = 0;
    *(undefined8 *)local_d8[1] = 0;
  }
  FUN_1403a0ba0(local_d8,uVar16,uVar13);
  local_120 = operator_new(0x18);
  if (local_120 != (undefined1 (*) [16])0x0) {
    *local_120 = (undefined1  [16])0x0;
    *(undefined8 *)*local_120 = 0;
    *(undefined8 *)(*local_120 + 8) = 0;
    *(undefined8 *)local_120[1] = 0;
  }
  FUN_1403a0ba0(local_120,uVar16,uVar13);
  local_d0 = operator_new(0x18);
  if (local_d0 != (undefined1 (*) [16])0x0) {
    *local_d0 = (undefined1  [16])0x0;
    *(undefined8 *)*local_d0 = 0;
    *(undefined8 *)(*local_d0 + 8) = 0;
    *(undefined8 *)local_d0[1] = 0;
  }
  FUN_1403a0ba0(local_d0,uVar16,uVar13);
  local_c8 = operator_new(0x18);
  if (local_c8 != (undefined1 (*) [16])0x0) {
    *local_c8 = (undefined1  [16])0x0;
    *(undefined8 *)*local_c8 = 0;
    *(undefined8 *)(*local_c8 + 8) = 0;
    *(undefined8 *)local_c8[1] = 0;
  }
  FUN_1403a0ba0(local_c8,uVar16,uVar13);
  local_b0 = *(int *)(lVar5 + 0x1018);
  local_138 = local_b0 - *(int *)(lVar5 + 0x1020);
  iVar14 = (local_b0 - *(int *)(lVar5 + 0x1028)) - *(int *)(lVar5 + 0x101c);
  local_b0 = local_b0 - *(int *)(lVar5 + 0x1024);
  uVar21 = 0;
  uVar13 = local_84;
  if (local_84 < local_7c - 1U) {
    do {
      puVar17 = (ushort *)
                ((ulonglong)(uVar13 * *(int *)(lVar6 + 0x14)) + (ulonglong)local_88 * 2 +
                *(longlong *)(lVar6 + 0x20));
      if (*(longlong *)pauVar7[1] == 0) {
        pfVar23 = (float *)0x0;
      }
      else if (uVar21 < *(uint *)(*pauVar7 + 4)) {
        pfVar23 = (float *)((ulonglong)(uVar21 * *(int *)(*pauVar7 + 8)) + *(longlong *)pauVar7[1]);
      }
      else {
        pfVar23 = (float *)0x0;
      }
      uVar16 = local_88;
      if (*(longlong *)pauVar25[1] == 0) {
        pfVar19 = (float *)0x0;
      }
      else if (uVar21 < *(uint *)(*pauVar25 + 4)) {
        pfVar19 = (float *)((ulonglong)(uVar21 * *(int *)(*pauVar25 + 8)) + *(longlong *)pauVar25[1]
                           );
      }
      else {
        pfVar19 = (float *)0x0;
      }
      for (; uVar16 < local_80; uVar16 = uVar16 + 2) {
        *pfVar23 = (float)*puVar17;
        pfVar23 = pfVar23 + 1;
        *pfVar19 = (float)puVar17[1];
        pfVar19 = pfVar19 + 1;
        puVar17 = puVar17 + 2;
      }
      puVar17 = (ushort *)
                ((ulonglong)((uVar13 + 1) * *(int *)(lVar6 + 0x14)) + (ulonglong)local_88 * 2 +
                *(longlong *)(lVar6 + 0x20));
      if (*(longlong *)pauVar27[1] == 0) {
        pfVar23 = (float *)0x0;
      }
      else if (uVar21 < *(uint *)(*pauVar27 + 4)) {
        pfVar23 = (float *)((ulonglong)(uVar21 * *(int *)(*pauVar27 + 8)) + *(longlong *)pauVar27[1]
                           );
      }
      else {
        pfVar23 = (float *)0x0;
      }
      uVar16 = local_88;
      if (*(longlong *)local_130[1] == 0) {
        pfVar19 = (float *)0x0;
      }
      else if (uVar21 < *(uint *)(*local_130 + 4)) {
        pfVar19 = (float *)((ulonglong)(uVar21 * *(int *)(*local_130 + 8)) +
                           *(longlong *)local_130[1]);
      }
      else {
        pfVar19 = (float *)0x0;
      }
      for (; uVar16 < local_80; uVar16 = uVar16 + 2) {
        *pfVar23 = (float)*puVar17;
        pfVar23 = pfVar23 + 1;
        *pfVar19 = (float)puVar17[1];
        pfVar19 = pfVar19 + 1;
        puVar17 = puVar17 + 2;
      }
      uVar13 = uVar13 + 2;
      uVar21 = uVar21 + 1;
      uVar16 = local_b8;
    } while (uVar13 < local_7c - 1U);
  }
  auVar1._4_8_ = SUB128(ZEXT812(0),4);
  auVar1._0_4_ = (float)local_138;
  local_b4 = iVar14;
  FUN_1403a2aa0(local_f0,local_e8,pauVar7,auVar1._0_8_);
  fVar30 = (float)iVar14;
  local_168 = 0xffffffff;
  local_170._0_4_ = 0;
  local_178._0_4_ = fVar30;
  FUN_1403a26f0(local_e0,local_128,pauVar25,pauVar27);
  local_168 = 0;
  local_170._0_4_ = 0xffffffff;
  local_178 = CONCAT44(local_178._4_4_,fVar30);
  FUN_1403a26f0(local_d8,local_120,pauVar27,pauVar25);
  iVar3 = local_b0;
  pauVar15 = local_130;
  auVar2._4_8_ = SUB128(ZEXT812(0),4);
  auVar2._0_4_ = (float)local_b0;
  FUN_1403a2aa0(local_d0,local_c8,local_130,auVar2._0_8_);
  lVar5 = *(longlong *)(local_118 + 0x68);
  local_178 = lVar5 + 0x600d0;
  local_160 = local_138;
  local_168 = *(undefined4 *)(lVar5 + 0xc00e8);
  local_170 = CONCAT44(local_170._4_4_,*(undefined4 *)(lVar5 + 0xc00f4));
  FUN_1403a1b00(local_108,local_f0,local_e8);
  lVar5 = *(longlong *)(local_118 + 0x68);
  local_170 = lVar5 + 0x800d0;
  local_178 = lVar5 + 0x200d0;
  local_148 = 0xffffffff;
  local_150 = 0;
  local_160 = *(undefined4 *)(lVar5 + 0xc00ec);
  local_168 = *(undefined4 *)(lVar5 + 0xc00f8);
  local_158 = iVar14;
  FUN_1403a0c30(local_100,local_e0,local_128,local_120);
  lVar5 = *(longlong *)(local_118 + 0x68);
  local_170 = lVar5 + 0x800d0;
  local_178 = lVar5 + 0x200d0;
  local_148 = 0;
  local_150 = 0xffffffff;
  local_160 = *(undefined4 *)(lVar5 + 0xc00ec);
  local_168 = *(undefined4 *)(lVar5 + 0xc00f8);
  local_158 = iVar14;
  FUN_1403a0c30(local_f8,local_d8,local_120,local_128);
  pauVar28 = local_110;
  lVar5 = *(longlong *)(local_118 + 0x68);
  local_178 = lVar5 + 0xa00d0;
  local_160 = iVar3;
  local_168 = *(undefined4 *)(lVar5 + 0xc00f0);
  local_170 = CONCAT44(local_170._4_4_,*(undefined4 *)(lVar5 + 0xc00fc));
  FUN_1403a1b00(local_110,local_d0,local_c8);
  uVar13 = 0;
  if (local_134 != 0) {
    do {
      puVar18 = (undefined2 *)
                ((ulonglong)((local_84 + uVar13 * 2) * *(int *)(local_98 + 0x14)) +
                 (ulonglong)local_88 * 2 + *(longlong *)(local_98 + 0x20));
      if (*(longlong *)pauVar7[1] == 0) {
        pfVar23 = (float *)0x0;
      }
      else if (uVar13 < *(uint *)(*pauVar7 + 4)) {
        pfVar23 = (float *)((ulonglong)(uVar13 * *(int *)(*pauVar7 + 8)) + *(longlong *)pauVar7[1]);
      }
      else {
        pfVar23 = (float *)0x0;
      }
      if (*(longlong *)pauVar25[1] == 0) {
        pfVar19 = (float *)0x0;
      }
      else if (uVar13 < *(uint *)(*pauVar25 + 4)) {
        pfVar19 = (float *)((ulonglong)(uVar13 * *(int *)(*pauVar25 + 8)) + *(longlong *)pauVar25[1]
                           );
      }
      else {
        pfVar19 = (float *)0x0;
      }
      if (*(longlong *)local_108[1] == 0) {
        pfVar9 = (float *)0x0;
      }
      else if (uVar13 < *(uint *)(*local_108 + 4)) {
        pfVar9 = (float *)((ulonglong)(uVar13 * *(int *)(*local_108 + 8)) +
                          *(longlong *)local_108[1]);
      }
      else {
        pfVar9 = (float *)0x0;
      }
      if (*(longlong *)local_100[1] == 0) {
        pfVar11 = (float *)0x0;
      }
      else if (uVar13 < *(uint *)(*local_100 + 4)) {
        pfVar11 = (float *)((ulonglong)(uVar13 * *(int *)(*local_100 + 8)) +
                           *(longlong *)local_100[1]);
      }
      else {
        pfVar11 = (float *)0x0;
      }
      uVar21 = 0;
      pfVar10 = pfVar9;
      pfVar12 = pfVar11;
      pfVar20 = pfVar19;
      pfVar24 = pfVar23;
      if (3 < uVar16) {
        fVar30 = fVar31 - fVar29;
        uVar21 = (uVar16 - 4 >> 2) + 1;
        uVar26 = (ulonglong)uVar21;
        uVar21 = uVar21 * 4;
        do {
          *puVar18 = (short)(int)(fVar30 * *pfVar24 + fVar29 * *pfVar10);
          puVar18[1] = (short)(int)(fVar30 * *pfVar20 + fVar29 * *pfVar12);
          puVar18[2] = (short)(int)(fVar29 * pfVar10[1] +
                                   fVar30 * *(float *)((longlong)pfVar10 +
                                                      (longlong)pfVar23 + (4 - (longlong)pfVar9)));
          puVar18[3] = (short)(int)(fVar29 * *(float *)((longlong)pfVar10 +
                                                       (longlong)pfVar11 + (4 - (longlong)pfVar9)) +
                                   fVar30 * *(float *)((longlong)pfVar10 +
                                                      (longlong)pfVar19 + (4 - (longlong)pfVar9)));
          puVar18[4] = (short)(int)(fVar30 * *(float *)((longlong)pfVar10 +
                                                       (longlong)pfVar23 + (8 - (longlong)pfVar9)) +
                                   fVar29 * pfVar10[2]);
          puVar18[5] = (short)(int)(fVar30 * *(float *)((longlong)pfVar10 +
                                                       (longlong)pfVar19 + (8 - (longlong)pfVar9)) +
                                   fVar29 * *(float *)((longlong)pfVar10 +
                                                      (longlong)pfVar11 + (8 - (longlong)pfVar9)));
          puVar18[6] = (short)(int)(fVar30 * *(float *)((longlong)pfVar10 +
                                                       (longlong)pfVar23 + (0xc - (longlong)pfVar9))
                                   + fVar29 * pfVar10[3]);
          puVar18[7] = (short)(int)(fVar29 * *(float *)((longlong)pfVar10 +
                                                       (longlong)pfVar11 + (0xc - (longlong)pfVar9))
                                   + fVar30 * *(float *)((longlong)pfVar10 +
                                                        (longlong)pfVar19 + (0xc - (longlong)pfVar9)
                                                        ));
          puVar18 = puVar18 + 8;
          pfVar12 = pfVar12 + 4;
          pfVar10 = pfVar10 + 4;
          pfVar20 = pfVar20 + 4;
          pfVar24 = pfVar24 + 4;
          uVar26 = uVar26 - 1;
          pauVar15 = local_130;
          pauVar27 = local_c0;
          pauVar28 = local_110;
        } while (uVar26 != 0);
      }
      if (uVar21 < uVar16) {
        lVar22 = (longlong)pfVar24 - (longlong)pfVar10;
        lVar6 = (longlong)pfVar20 - (longlong)pfVar10;
        lVar5 = (longlong)pfVar12 - (longlong)pfVar10;
        uVar26 = (ulonglong)(uVar16 - uVar21);
        do {
          *puVar18 = (short)(int)((fVar31 - fVar29) * *(float *)((longlong)pfVar10 + lVar22) +
                                 fVar29 * *pfVar10);
          puVar18[1] = (short)(int)(fVar29 * *(float *)((longlong)pfVar10 + lVar5) +
                                   (fVar31 - fVar29) * *(float *)((longlong)pfVar10 + lVar6));
          puVar18 = puVar18 + 2;
          pfVar10 = pfVar10 + 1;
          uVar26 = uVar26 - 1;
        } while (uVar26 != 0);
      }
      puVar18 = (undefined2 *)
                ((ulonglong)((local_84 + 1 + uVar13 * 2) * *(int *)(local_98 + 0x14)) +
                 (ulonglong)local_88 * 2 + *(longlong *)(local_98 + 0x20));
      if (*(longlong *)pauVar27[1] == 0) {
        pfVar23 = (float *)0x0;
      }
      else if (uVar13 < *(uint *)(*pauVar27 + 4)) {
        pfVar23 = (float *)((ulonglong)(uVar13 * *(int *)(*pauVar27 + 8)) + *(longlong *)pauVar27[1]
                           );
      }
      else {
        pfVar23 = (float *)0x0;
      }
      if (*(longlong *)pauVar15[1] == 0) {
        pfVar19 = (float *)0x0;
      }
      else if (uVar13 < *(uint *)(*pauVar15 + 4)) {
        pfVar19 = (float *)((ulonglong)(uVar13 * *(int *)(*pauVar15 + 8)) + *(longlong *)pauVar15[1]
                           );
      }
      else {
        pfVar19 = (float *)0x0;
      }
      if (*(longlong *)local_f8[1] == 0) {
        pfVar9 = (float *)0x0;
      }
      else if (uVar13 < *(uint *)(*local_f8 + 4)) {
        pfVar9 = (float *)((ulonglong)(uVar13 * *(int *)(*local_f8 + 8)) + *(longlong *)local_f8[1])
        ;
      }
      else {
        pfVar9 = (float *)0x0;
      }
      if (*(longlong *)pauVar28[1] == 0) {
        pfVar11 = (float *)0x0;
      }
      else if (uVar13 < *(uint *)(*pauVar28 + 4)) {
        pfVar11 = (float *)((ulonglong)(uVar13 * *(int *)(*pauVar28 + 8)) + *(longlong *)pauVar28[1]
                           );
      }
      else {
        pfVar11 = (float *)0x0;
      }
      uVar21 = 0;
      pfVar10 = pfVar9;
      pfVar12 = pfVar11;
      pfVar20 = pfVar19;
      pfVar24 = pfVar23;
      if (3 < uVar16) {
        fVar30 = fVar31 - fVar29;
        uVar21 = (uVar16 - 4 >> 2) + 1;
        uVar26 = (ulonglong)uVar21;
        uVar21 = uVar21 * 4;
        do {
          *puVar18 = (short)(int)(fVar29 * *pfVar10 + fVar30 * *pfVar24);
          puVar18[1] = (short)(int)(fVar29 * *pfVar12 + fVar30 * *pfVar20);
          puVar18[2] = (short)(int)(fVar29 * pfVar10[1] +
                                   fVar30 * *(float *)((longlong)pfVar10 +
                                                      (longlong)pfVar23 + (4 - (longlong)pfVar9)));
          puVar18[3] = (short)(int)(fVar29 * *(float *)((longlong)pfVar10 +
                                                       (longlong)pfVar11 + (4 - (longlong)pfVar9)) +
                                   fVar30 * *(float *)((longlong)pfVar10 +
                                                      (longlong)pfVar19 + (4 - (longlong)pfVar9)));
          puVar18[4] = (short)(int)(fVar30 * *(float *)((longlong)pfVar10 +
                                                       (longlong)pfVar23 + (8 - (longlong)pfVar9)) +
                                   fVar29 * pfVar10[2]);
          puVar18[5] = (short)(int)(fVar30 * *(float *)((longlong)pfVar10 +
                                                       (longlong)pfVar19 + (8 - (longlong)pfVar9)) +
                                   fVar29 * *(float *)((longlong)pfVar10 +
                                                      (longlong)pfVar11 + (8 - (longlong)pfVar9)));
          puVar18[6] = (short)(int)(fVar30 * *(float *)((longlong)pfVar10 +
                                                       (longlong)pfVar23 + (0xc - (longlong)pfVar9))
                                   + fVar29 * pfVar10[3]);
          puVar18[7] = (short)(int)(fVar29 * *(float *)((longlong)pfVar10 +
                                                       (longlong)pfVar11 + (0xc - (longlong)pfVar9))
                                   + fVar30 * *(float *)((longlong)pfVar10 +
                                                        (longlong)pfVar19 + (0xc - (longlong)pfVar9)
                                                        ));
          puVar18 = puVar18 + 8;
          pfVar12 = pfVar12 + 4;
          pfVar10 = pfVar10 + 4;
          pfVar20 = pfVar20 + 4;
          pfVar24 = pfVar24 + 4;
          uVar26 = uVar26 - 1;
          pauVar15 = local_130;
          pauVar27 = local_c0;
          pauVar28 = local_110;
        } while (uVar26 != 0);
      }
      if (uVar21 < uVar16) {
        lVar22 = (longlong)pfVar24 - (longlong)pfVar10;
        lVar6 = (longlong)pfVar20 - (longlong)pfVar10;
        lVar5 = (longlong)pfVar12 - (longlong)pfVar10;
        uVar26 = (ulonglong)(uVar16 - uVar21);
        do {
          *puVar18 = (short)(int)((fVar31 - fVar29) * *(float *)((longlong)pfVar10 + lVar22) +
                                 fVar29 * *pfVar10);
          puVar18[1] = (short)(int)(fVar29 * *(float *)((longlong)pfVar10 + lVar5) +
                                   (fVar31 - fVar29) * *(float *)((longlong)pfVar10 + lVar6));
          puVar18 = puVar18 + 2;
          pfVar10 = pfVar10 + 1;
          uVar26 = uVar26 - 1;
        } while (uVar26 != 0);
      }
      uVar13 = uVar13 + 1;
      pauVar7 = local_a8;
    } while (uVar13 < local_134);
  }
  if (pauVar7 != (undefined1 (*) [16])0x0) {
    lVar5 = *(longlong *)pauVar7[1];
    if (lVar5 != 0) {
      uVar8 = FUN_1401540a0();
      FUN_140153f60(uVar8,lVar5);
    }
    operator_delete(pauVar7);
  }
  if (pauVar25 != (undefined1 (*) [16])0x0) {
    lVar5 = *(longlong *)pauVar25[1];
    if (lVar5 != 0) {
      uVar8 = FUN_1401540a0();
      FUN_140153f60(uVar8,lVar5);
    }
    operator_delete(pauVar25);
  }
  if (pauVar27 != (undefined1 (*) [16])0x0) {
    lVar5 = *(longlong *)pauVar27[1];
    if (lVar5 != 0) {
      uVar8 = FUN_1401540a0();
      FUN_140153f60(uVar8,lVar5);
    }
    operator_delete(pauVar27);
  }
  if (pauVar15 != (undefined1 (*) [16])0x0) {
    lVar5 = *(longlong *)pauVar15[1];
    if (lVar5 != 0) {
      uVar8 = FUN_1401540a0();
      FUN_140153f60(uVar8,lVar5);
    }
    operator_delete(pauVar15);
  }
  pauVar25 = local_108;
  if (local_108 != (undefined1 (*) [16])0x0) {
    lVar5 = *(longlong *)local_108[1];
    if (lVar5 != 0) {
      uVar8 = FUN_1401540a0();
      FUN_140153f60(uVar8,lVar5);
    }
    operator_delete(pauVar25);
  }
  pauVar25 = local_100;
  if (local_100 != (undefined1 (*) [16])0x0) {
    lVar5 = *(longlong *)local_100[1];
    if (lVar5 != 0) {
      uVar8 = FUN_1401540a0();
      FUN_140153f60(uVar8,lVar5);
    }
    operator_delete(pauVar25);
  }
  pauVar25 = local_f8;
  if (local_f8 != (undefined1 (*) [16])0x0) {
    lVar5 = *(longlong *)local_f8[1];
    if (lVar5 != 0) {
      uVar8 = FUN_1401540a0();
      FUN_140153f60(uVar8,lVar5);
    }
    operator_delete(pauVar25);
  }
  if (pauVar28 != (undefined1 (*) [16])0x0) {
    lVar5 = *(longlong *)pauVar28[1];
    if (lVar5 != 0) {
      uVar8 = FUN_1401540a0();
      FUN_140153f60(uVar8,lVar5);
    }
    operator_delete(pauVar28);
  }
  pauVar25 = local_f0;
  if (local_f0 != (undefined1 (*) [16])0x0) {
    lVar5 = *(longlong *)local_f0[1];
    if (lVar5 != 0) {
      uVar8 = FUN_1401540a0();
      FUN_140153f60(uVar8,lVar5);
    }
    operator_delete(pauVar25);
  }
  pauVar25 = local_e8;
  if (local_e8 != (undefined1 (*) [16])0x0) {
    lVar5 = *(longlong *)local_e8[1];
    if (lVar5 != 0) {
      uVar8 = FUN_1401540a0();
      FUN_140153f60(uVar8,lVar5);
    }
    operator_delete(pauVar25);
  }
  pauVar25 = local_e0;
  if (local_e0 != (undefined1 (*) [16])0x0) {
    lVar5 = *(longlong *)local_e0[1];
    if (lVar5 != 0) {
      uVar8 = FUN_1401540a0();
      FUN_140153f60(uVar8,lVar5);
    }
    operator_delete(pauVar25);
  }
  pauVar25 = local_128;
  if (local_128 != (undefined1 (*) [16])0x0) {
    lVar5 = *(longlong *)local_128[1];
    if (lVar5 != 0) {
      uVar8 = FUN_1401540a0();
      FUN_140153f60(uVar8,lVar5);
    }
    operator_delete(pauVar25);
  }
  pauVar25 = local_d8;
  if (local_d8 != (undefined1 (*) [16])0x0) {
    lVar5 = *(longlong *)local_d8[1];
    if (lVar5 != 0) {
      uVar8 = FUN_1401540a0();
      FUN_140153f60(uVar8,lVar5);
    }
    operator_delete(pauVar25);
  }
  pauVar25 = local_120;
  if (local_120 != (undefined1 (*) [16])0x0) {
    lVar5 = *(longlong *)local_120[1];
    if (lVar5 != 0) {
      uVar8 = FUN_1401540a0();
      FUN_140153f60(uVar8,lVar5);
    }
    operator_delete(pauVar25);
  }
  pauVar25 = local_d0;
  if (local_d0 != (undefined1 (*) [16])0x0) {
    lVar5 = *(longlong *)local_d0[1];
    if (lVar5 != 0) {
      uVar8 = FUN_1401540a0();
      FUN_140153f60(uVar8,lVar5);
    }
    operator_delete(pauVar25);
  }
  pauVar25 = local_c8;
  if (local_c8 != (undefined1 (*) [16])0x0) {
    lVar5 = *(longlong *)local_c8[1];
    if (lVar5 != 0) {
      uVar8 = FUN_1401540a0();
      FUN_140153f60(uVar8,lVar5);
    }
    operator_delete(pauVar25);
  }
  lVar5 = local_118;
  FUN_14016b640(local_118,local_a0,0);
  local_178 = CONCAT44(local_178._4_4_,0x10);
  (*(code *)local_90[0xb])(&local_90,0x10,0x10,0x10);
  *(uint *)(lVar5 + 0x30) = local_88;
  *(uint *)(lVar5 + 0x38) = local_80;
  *(uint *)(lVar5 + 0x34) = local_84;
  *(int *)(lVar5 + 0x3c) = local_7c;
  return 0;
}



// ===== depth0 FUN_1403b4bb0 @ 0x1403b4bb0 rva=0x3b4bb0 size=2950 =====

/* WARNING: Function: __security_check_cookie replaced with injection: security_check_cookie */

undefined8 FUN_1403b4bb0(undefined8 param_1,longlong param_2,undefined8 *param_3)

{
  longlong *plVar1;
  undefined1 uVar2;
  int iVar3;
  uint uVar4;
  longlong lVar5;
  undefined ***pppuVar6;
  undefined ***pppuVar7;
  undefined ***pppuVar8;
  undefined ***pppuVar9;
  undefined ***pppuVar10;
  undefined ***pppuVar11;
  undefined ***pppuVar12;
  undefined8 uVar13;
  undefined4 *puVar14;
  uint uVar15;
  undefined4 *puVar16;
  undefined4 *puVar17;
  undefined1 auStack_3c8 [32];
  undefined ***local_3a8;
  undefined ***local_3a0;
  undefined ***local_398;
  undefined ***local_390;
  undefined ***local_388;
  undefined ***local_380;
  undefined ***local_378;
  undefined ***local_370;
  int *local_368;
  undefined ***local_358;
  undefined ***local_350;
  undefined ***local_348;
  undefined ***local_340;
  undefined ***local_338;
  undefined ***local_330;
  undefined ***local_328;
  undefined ***local_320;
  undefined ***local_318;
  undefined ***local_310;
  undefined ***local_308;
  undefined8 *local_300;
  undefined **local_2f8;
  undefined4 local_2f0;
  undefined4 local_2ec;
  undefined4 local_2e8;
  undefined4 local_2e4;
  longlong local_2e0;
  int local_2d8;
  int local_2d4;
  int local_2d0;
  int local_2cc;
  int local_2b0;
  undefined2 local_78 [4];
  undefined **local_70;
  undefined4 local_68;
  undefined4 local_64;
  undefined4 local_60;
  undefined4 local_5c;
  undefined1 local_58 [8];
  undefined1 local_50 [8];
  ulonglong local_48;
  
  local_48 = DAT_140559440 ^ (ulonglong)auStack_3c8;
  local_2e0 = param_2;
  lVar5 = FUN_140152710(*(undefined8 *)(param_2 + 8),0);
  local_70 = sony_zhacai::ZcRectT<int>::vftable;
  local_68 = *(undefined4 *)(param_2 + 0x30);
  local_64 = *(undefined4 *)(param_2 + 0x34);
  local_60 = *(undefined4 *)(param_2 + 0x38);
  local_5c = *(undefined4 *)(param_2 + 0x3c);
  FUN_1403bea30(&local_2d8,*param_3,param_3,param_2);
  local_358 = operator_new(0x38);
  pppuVar12 = (undefined ***)0x0;
  pppuVar6 = pppuVar12;
  if (local_358 != (undefined ***)0x0) {
    *local_358 = (undefined **)0x0;
    local_358[1] = (undefined **)0x0;
    local_358[2] = (undefined **)0x0;
    local_358[3] = (undefined **)0x0;
    local_358[4] = (undefined **)0x0;
    local_358[5] = (undefined **)0x0;
    local_358[6] = (undefined **)0x0;
    pppuVar6 = (undefined ***)FUN_140152c40(local_358,1);
  }
  local_350 = pppuVar6;
  FUN_140152cb0(pppuVar6,*(undefined4 *)(lVar5 + 8),*(undefined4 *)(lVar5 + 0xc));
  FUN_140152ed0(pppuVar6);
  (**(code **)(*(longlong *)*param_3 + 0x1b0))((longlong *)*param_3,local_50,local_58,local_78);
  plVar1 = (longlong *)*param_3;
  iVar3 = (**(code **)(*plVar1 + 0x18))(plVar1);
  if ((iVar3 == 0x188) && (uVar4 = (**(code **)(*plVar1 + 0xe0))(plVar1), 0x640 < uVar4)) {
    uVar2 = 1;
  }
  else {
    uVar2 = 0;
  }
  local_3a8 = (undefined ***)CONCAT71(local_3a8._1_7_,uVar2);
  FUN_1403b49e0(lVar5,pppuVar6,&local_2d8,local_78[0]);
  local_358 = operator_new(0x38);
  pppuVar7 = pppuVar12;
  if (local_358 != (undefined ***)0x0) {
    *local_358 = (undefined **)0x0;
    local_358[1] = (undefined **)0x0;
    local_358[2] = (undefined **)0x0;
    local_358[3] = (undefined **)0x0;
    local_358[4] = (undefined **)0x0;
    local_358[5] = (undefined **)0x0;
    local_358[6] = (undefined **)0x0;
    pppuVar7 = (undefined ***)FUN_140152c40(local_358,1);
  }
  FUN_140152cb0(pppuVar7,*(uint *)(lVar5 + 8) >> 1,*(undefined4 *)(lVar5 + 0xc));
  FUN_140152ed0(pppuVar7);
  if (*(int *)((longlong)pppuVar7 + 0xc) != 0) {
    uVar4 = *(uint *)(pppuVar7 + 1);
    pppuVar8 = pppuVar12;
    do {
      iVar3 = (int)pppuVar8;
      puVar16 = (undefined4 *)
                ((ulonglong)(uint)(iVar3 * *(int *)((longlong)pppuVar6 + 0x14)) +
                (longlong)pppuVar6[4]);
      puVar14 = (undefined4 *)
                ((ulonglong)(uint)(iVar3 * *(int *)((longlong)pppuVar7 + 0x14)) +
                (longlong)pppuVar7[4]);
      puVar17 = puVar16 + 1;
      if (((ulonglong)pppuVar8 & 1) != 0) {
        puVar17 = puVar16;
      }
      pppuVar8 = pppuVar12;
      if (uVar4 != 0) {
        do {
          *puVar14 = *puVar17;
          puVar14 = puVar14 + 1;
          puVar17 = puVar17 + 2;
          uVar15 = (int)pppuVar8 + 1;
          uVar4 = *(uint *)(pppuVar7 + 1);
          pppuVar8 = (undefined ***)(ulonglong)uVar15;
        } while (uVar15 < uVar4);
      }
      pppuVar8 = (undefined ***)(ulonglong)(iVar3 + 1U);
    } while (iVar3 + 1U < *(uint *)((longlong)pppuVar7 + 0xc));
  }
  local_318 = (undefined ***)0x0;
  if ((local_2d8 == 1) && (local_2b0 == 1)) {
    local_358 = operator_new(0x38);
    pppuVar8 = pppuVar12;
    if (local_358 != (undefined ***)0x0) {
      *local_358 = (undefined **)0x0;
      local_358[1] = (undefined **)0x0;
      local_358[2] = (undefined **)0x0;
      local_358[3] = (undefined **)0x0;
      local_358[4] = (undefined **)0x0;
      local_358[5] = (undefined **)0x0;
      local_358[6] = (undefined **)0x0;
      pppuVar8 = (undefined ***)FUN_140152c40(local_358,1);
    }
    local_318 = pppuVar8;
    FUN_140152cb0(pppuVar8,*(uint *)(lVar5 + 8) >> 1,*(undefined4 *)(lVar5 + 0xc));
    FUN_140152ed0(pppuVar8);
    local_2f8 = sony_zhacai::ZcRectT<int>::vftable;
    local_2f0 = *(undefined4 *)(param_2 + 0x48);
    local_2ec = *(undefined4 *)(param_2 + 0x4c);
    local_2e8 = *(undefined4 *)(param_2 + 0x50);
    local_2e4 = *(undefined4 *)(param_2 + 0x54);
    local_3a8 = &local_2f8;
    FUN_1403b5740(pppuVar6,pppuVar7,pppuVar8,&local_2d8);
  }
  local_358 = operator_new(0x38);
  pppuVar8 = pppuVar12;
  if (local_358 != (undefined ***)0x0) {
    *local_358 = (undefined **)0x0;
    local_358[1] = (undefined **)0x0;
    local_358[2] = (undefined **)0x0;
    local_358[3] = (undefined **)0x0;
    local_358[4] = (undefined **)0x0;
    local_358[5] = (undefined **)0x0;
    local_358[6] = (undefined **)0x0;
    pppuVar8 = (undefined ***)FUN_140152c40(local_358,1);
  }
  local_320 = pppuVar8;
  FUN_140152cb0(pppuVar8,*(uint *)(lVar5 + 8) >> 1,*(undefined4 *)(lVar5 + 0xc));
  local_358 = operator_new(0x38);
  pppuVar9 = pppuVar12;
  if (local_358 != (undefined ***)0x0) {
    *local_358 = (undefined **)0x0;
    local_358[1] = (undefined **)0x0;
    local_358[2] = (undefined **)0x0;
    local_358[3] = (undefined **)0x0;
    local_358[4] = (undefined **)0x0;
    local_358[5] = (undefined **)0x0;
    local_358[6] = (undefined **)0x0;
    pppuVar9 = (undefined ***)FUN_140152c40(local_358,1);
  }
  local_328 = pppuVar9;
  FUN_140152cb0(pppuVar9,*(uint *)(lVar5 + 8) >> 1,*(undefined4 *)(lVar5 + 0xc));
  local_358 = operator_new(0x38);
  pppuVar10 = pppuVar12;
  if (local_358 != (undefined ***)0x0) {
    *local_358 = (undefined **)0x0;
    local_358[1] = (undefined **)0x0;
    local_358[2] = (undefined **)0x0;
    local_358[3] = (undefined **)0x0;
    local_358[4] = (undefined **)0x0;
    local_358[5] = (undefined **)0x0;
    local_358[6] = (undefined **)0x0;
    pppuVar10 = (undefined ***)FUN_140152c40(local_358,1);
  }
  local_330 = pppuVar10;
  FUN_140152cb0(pppuVar10,*(uint *)(lVar5 + 8) >> 1,*(undefined4 *)(lVar5 + 0xc));
  FUN_140152ed0(pppuVar8);
  FUN_140152ed0(pppuVar9);
  FUN_140152ed0(pppuVar10);
  if (local_2d8 == 1) {
    local_3a0 = (undefined ***)&local_2d8;
    local_3a8 = pppuVar10;
    FUN_1403b6b80(pppuVar6,pppuVar7,pppuVar8,pppuVar9);
  }
  pppuVar6 = pppuVar12;
  if (((local_2d8 == 1) && (local_2d0 == 1)) && (pppuVar6 = (undefined ***)0x0, local_2cc == 0)) {
    local_358 = operator_new(0x38);
    pppuVar6 = pppuVar12;
    if (local_358 != (undefined ***)0x0) {
      *local_358 = (undefined **)0x0;
      local_358[1] = (undefined **)0x0;
      local_358[2] = (undefined **)0x0;
      local_358[3] = (undefined **)0x0;
      local_358[4] = (undefined **)0x0;
      local_358[5] = (undefined **)0x0;
      local_358[6] = (undefined **)0x0;
      pppuVar6 = (undefined ***)FUN_140152c40(local_358,0);
    }
    FUN_140152cb0(pppuVar6,*(uint *)(lVar5 + 8) >> 1,*(undefined4 *)(lVar5 + 0xc));
    local_358 = operator_new(0x38);
    local_348 = pppuVar12;
    if (local_358 != (undefined ***)0x0) {
      *local_358 = (undefined **)0x0;
      local_358[1] = (undefined **)0x0;
      local_358[2] = (undefined **)0x0;
      local_358[3] = (undefined **)0x0;
      local_358[4] = (undefined **)0x0;
      local_358[5] = (undefined **)0x0;
      local_358[6] = (undefined **)0x0;
      local_348 = (undefined ***)FUN_140152c40(local_358,1);
    }
    FUN_140152cb0(local_348,*(uint *)(lVar5 + 8) >> 1,*(undefined4 *)(lVar5 + 0xc));
    local_358 = operator_new(0x38);
    pppuVar9 = pppuVar12;
    if (local_358 != (undefined ***)0x0) {
      *local_358 = (undefined **)0x0;
      local_358[1] = (undefined **)0x0;
      local_358[2] = (undefined **)0x0;
      local_358[3] = (undefined **)0x0;
      local_358[4] = (undefined **)0x0;
      local_358[5] = (undefined **)0x0;
      local_358[6] = (undefined **)0x0;
      pppuVar9 = (undefined ***)FUN_140152c40(local_358,1);
    }
    local_310 = pppuVar9;
    FUN_140152cb0(pppuVar9,*(uint *)(lVar5 + 8) >> 1,*(undefined4 *)(lVar5 + 0xc));
    local_358 = operator_new(0x38);
    pppuVar10 = pppuVar12;
    if (local_358 != (undefined ***)0x0) {
      *local_358 = (undefined **)0x0;
      local_358[1] = (undefined **)0x0;
      local_358[2] = (undefined **)0x0;
      local_358[3] = (undefined **)0x0;
      local_358[4] = (undefined **)0x0;
      local_358[5] = (undefined **)0x0;
      local_358[6] = (undefined **)0x0;
      pppuVar10 = (undefined ***)FUN_140152c40(local_358,1);
    }
    local_308 = pppuVar10;
    FUN_140152cb0(pppuVar10,*(uint *)(lVar5 + 8) >> 1,*(undefined4 *)(lVar5 + 0xc));
    FUN_140152ed0(pppuVar6);
    FUN_140152ed0(local_348);
    FUN_140152ed0(pppuVar9);
    FUN_140152ed0(pppuVar10);
    local_388 = (undefined ***)&local_2d8;
    local_3a0 = local_348;
    local_3a8 = pppuVar6;
    local_398 = pppuVar9;
    local_390 = pppuVar10;
    FUN_1403b99a0(local_350,pppuVar8,local_328,local_330);
    local_358 = operator_new(0x38);
    local_340 = pppuVar12;
    if (local_358 != (undefined ***)0x0) {
      *local_358 = (undefined **)0x0;
      local_358[1] = (undefined **)0x0;
      local_358[2] = (undefined **)0x0;
      local_358[3] = (undefined **)0x0;
      local_358[4] = (undefined **)0x0;
      local_358[5] = (undefined **)0x0;
      local_358[6] = (undefined **)0x0;
      local_340 = (undefined ***)FUN_140152c40(local_358,1);
    }
    FUN_140152cb0(local_340,*(uint *)(lVar5 + 8) >> 1,*(undefined4 *)(lVar5 + 0xc));
    local_358 = operator_new(0x38);
    local_338 = pppuVar12;
    if (local_358 != (undefined ***)0x0) {
      *local_358 = (undefined **)0x0;
      local_358[1] = (undefined **)0x0;
      local_358[2] = (undefined **)0x0;
      local_358[3] = (undefined **)0x0;
      local_358[4] = (undefined **)0x0;
      local_358[5] = (undefined **)0x0;
      local_358[6] = (undefined **)0x0;
      local_338 = (undefined ***)FUN_140152c40(local_358,1);
    }
    FUN_140152cb0(local_338,*(uint *)(lVar5 + 8) >> 1,*(undefined4 *)(lVar5 + 0xc));
    local_358 = operator_new(0x38);
    pppuVar8 = pppuVar12;
    if (local_358 != (undefined ***)0x0) {
      *local_358 = (undefined **)0x0;
      local_358[1] = (undefined **)0x0;
      local_358[2] = (undefined **)0x0;
      local_358[3] = (undefined **)0x0;
      local_358[4] = (undefined **)0x0;
      local_358[5] = (undefined **)0x0;
      local_358[6] = (undefined **)0x0;
      pppuVar8 = (undefined ***)FUN_140152c40(local_358,1);
    }
    FUN_140152cb0(pppuVar8,*(uint *)(lVar5 + 8) >> 1,*(undefined4 *)(lVar5 + 0xc));
    local_358 = operator_new(0x38);
    pppuVar9 = pppuVar12;
    if (local_358 != (undefined ***)0x0) {
      *local_358 = (undefined **)0x0;
      local_358[1] = (undefined **)0x0;
      local_358[2] = (undefined **)0x0;
      local_358[3] = (undefined **)0x0;
      local_358[4] = (undefined **)0x0;
      local_358[5] = (undefined **)0x0;
      local_358[6] = (undefined **)0x0;
      pppuVar9 = (undefined ***)FUN_140152c40(local_358,1);
    }
    FUN_140152cb0(pppuVar9,*(uint *)(lVar5 + 8) >> 1,*(undefined4 *)(lVar5 + 0xc));
    local_358 = operator_new(0x38);
    pppuVar10 = pppuVar12;
    if (local_358 != (undefined ***)0x0) {
      *local_358 = (undefined **)0x0;
      local_358[1] = (undefined **)0x0;
      local_358[2] = (undefined **)0x0;
      local_358[3] = (undefined **)0x0;
      local_358[4] = (undefined **)0x0;
      local_358[5] = (undefined **)0x0;
      local_358[6] = (undefined **)0x0;
      pppuVar10 = (undefined ***)FUN_140152c40(local_358,1);
    }
    local_358 = pppuVar10;
    FUN_140152cb0(pppuVar10,*(uint *)(lVar5 + 8) >> 1,*(undefined4 *)(lVar5 + 0xc));
    FUN_140152ed0(local_340);
    FUN_140152ed0(local_338);
    FUN_140152ed0(pppuVar8);
    FUN_140152ed0(pppuVar9);
    FUN_140152ed0(pppuVar10);
    local_378 = (undefined ***)&local_2d8;
    local_398 = local_338;
    local_3a0 = local_340;
    local_3a8 = local_330;
    local_390 = pppuVar8;
    local_388 = pppuVar9;
    local_380 = pppuVar10;
    FUN_1403b8a20(local_350,pppuVar6,local_320,local_328);
    if (pppuVar6 != (undefined ***)0x0) {
      (*(code *)**pppuVar6)(pppuVar6,1);
    }
    local_300 = operator_new(0x38);
    pppuVar10 = pppuVar12;
    if (local_300 != (undefined8 *)0x0) {
      *local_300 = 0;
      local_300[1] = 0;
      local_300[2] = 0;
      local_300[3] = 0;
      local_300[4] = 0;
      local_300[5] = 0;
      local_300[6] = 0;
      pppuVar10 = (undefined ***)FUN_140152c40(local_300,0);
    }
    FUN_140152cb0(pppuVar10,*(uint *)(lVar5 + 8) >> 1,*(undefined4 *)(lVar5 + 0xc));
    local_300 = operator_new(0x38);
    pppuVar11 = pppuVar12;
    if (local_300 != (undefined8 *)0x0) {
      *local_300 = 0;
      local_300[1] = 0;
      local_300[2] = 0;
      local_300[3] = 0;
      local_300[4] = 0;
      local_300[5] = 0;
      local_300[6] = 0;
      pppuVar11 = (undefined ***)FUN_140152c40(local_300,1);
    }
    FUN_140152cb0(pppuVar11,*(uint *)(lVar5 + 8) >> 1,*(undefined4 *)(lVar5 + 0xc));
    FUN_140152ed0(pppuVar10);
    FUN_140152ed0(pppuVar11);
    local_368 = &local_2d8;
    local_380 = local_358;
    local_398 = local_338;
    local_3a0 = local_308;
    local_3a8 = local_310;
    local_390 = pppuVar8;
    local_388 = pppuVar9;
    local_378 = pppuVar10;
    local_370 = pppuVar11;
    FUN_1403b7500(local_350,local_340,local_320,local_348);
    if (local_348 != (undefined ***)0x0) {
      (*(code *)**local_348)(local_348,1);
    }
    if (local_310 != (undefined ***)0x0) {
      (*(code *)**local_310)(local_310,1);
    }
    if (local_308 != (undefined ***)0x0) {
      (*(code *)**local_308)(local_308,1);
    }
    if (local_340 != (undefined ***)0x0) {
      (*(code *)**local_340)(local_340,1);
    }
    if (local_338 != (undefined ***)0x0) {
      (*(code *)**local_338)(local_338,1);
    }
    if (pppuVar8 != (undefined ***)0x0) {
      (*(code *)**pppuVar8)(pppuVar8,1);
    }
    if (pppuVar9 != (undefined ***)0x0) {
      (*(code *)**pppuVar9)(pppuVar9,1);
    }
    if (local_358 != (undefined ***)0x0) {
      (*(code *)**local_358)(local_358,1);
    }
    local_300 = operator_new(0x38);
    pppuVar6 = pppuVar12;
    if (local_300 != (undefined8 *)0x0) {
      *local_300 = 0;
      local_300[1] = 0;
      local_300[2] = 0;
      local_300[3] = 0;
      local_300[4] = 0;
      local_300[5] = 0;
      local_300[6] = 0;
      pppuVar6 = (undefined ***)FUN_140152c40(local_300,1);
    }
    FUN_140152cb0(pppuVar6,*(uint *)(lVar5 + 8) >> 1,*(undefined4 *)(lVar5 + 0xc));
    FUN_140152ed0(pppuVar6);
    pppuVar8 = local_320;
    local_3a0 = (undefined ***)&local_2d8;
    local_3a8 = pppuVar6;
    FUN_1403b85b0(local_350,pppuVar10,pppuVar11,local_320);
    if (pppuVar10 != (undefined ***)0x0) {
      (*(code *)**pppuVar10)(pppuVar10,1);
    }
    pppuVar9 = local_328;
    pppuVar10 = local_330;
    if (pppuVar11 != (undefined ***)0x0) {
      (*(code *)**pppuVar11)(pppuVar11,1);
      pppuVar9 = local_328;
      pppuVar10 = local_330;
    }
  }
  if (pppuVar9 != (undefined ***)0x0) {
    (*(code *)**pppuVar9)(pppuVar9,1);
  }
  if (pppuVar10 != (undefined ***)0x0) {
    (*(code *)**pppuVar10)(pppuVar10,1);
  }
  if ((local_2d8 == 0) || (pppuVar9 = local_318, local_2d4 == 0)) {
    pppuVar9 = pppuVar7;
  }
  if ((local_2d8 == 0) || (pppuVar10 = pppuVar6, local_2d0 == 0)) {
    pppuVar10 = pppuVar8;
  }
  local_300 = operator_new(0x30);
  if (local_300 != (void *)0x0) {
    pppuVar12 = (undefined ***)FUN_140152660(local_300);
  }
  local_3a0 = (undefined ***)CONCAT44(local_3a0._4_4_,1);
  local_3a8 = (undefined ***)((ulonglong)local_3a8 & 0xffffffffffffff00);
  FUN_1401527f0(pppuVar12,0,*(undefined4 *)(lVar5 + 8),*(undefined4 *)(lVar5 + 0xc));
  uVar13 = FUN_140152710(pppuVar12,0);
  pppuVar11 = local_350;
  local_3a8 = (undefined ***)&local_2d8;
  FUN_1403b6050(pppuVar9,pppuVar10,local_350,uVar13);
  if (pppuVar8 != (undefined ***)0x0) {
    (*(code *)**pppuVar8)(pppuVar8,1);
  }
  (*(code *)**pppuVar7)(pppuVar7,1);
  if (pppuVar11 != (undefined ***)0x0) {
    (*(code *)**pppuVar11)(pppuVar11,1);
  }
  if (local_318 != (undefined ***)0x0) {
    (*(code *)**local_318)(local_318,1);
  }
  if (pppuVar6 != (undefined ***)0x0) {
    (*(code *)**pppuVar6)(pppuVar6,1);
  }
  lVar5 = local_2e0;
  FUN_14016b640(local_2e0,pppuVar12,0);
  local_3a8 = (undefined ***)CONCAT44(local_3a8._4_4_,0x10);
  (*(code *)local_70[0xb])(&local_70,0x10,0x10,0x10);
  *(undefined4 *)(lVar5 + 0x30) = local_68;
  *(undefined4 *)(lVar5 + 0x38) = local_60;
  *(undefined4 *)(lVar5 + 0x34) = local_64;
  *(undefined4 *)(lVar5 + 0x3c) = local_5c;
  return 0;
}



// ===== depth0 FUN_140385af0 @ 0x140385af0 rva=0x385af0 size=373 =====

/* WARNING: Function: __security_check_cookie replaced with injection: security_check_cookie */

undefined8 FUN_140385af0(undefined8 param_1,longlong param_2,longlong param_3,longlong param_4)

{
  ushort uVar1;
  longlong lVar2;
  int iVar3;
  uint uVar4;
  longlong lVar5;
  int iVar6;
  ulonglong uVar7;
  int iVar8;
  uint uVar9;
  undefined1 auStack_88 [32];
  longlong local_68;
  longlong local_60;
  int local_58;
  int local_54;
  uint local_50;
  int local_4c;
  ulonglong local_48 [2];
  
  local_48[0] = DAT_140559440 ^ (ulonglong)auStack_88;
  iVar8 = *(int *)(param_4 + 0xc);
  if (iVar8 < *(int *)(param_4 + 0x14)) {
    iVar6 = *(int *)(param_4 + 0x10);
    iVar3 = iVar8;
    local_68 = param_2;
    local_60 = param_3;
    do {
      iVar3 = iVar3 + 1;
      uVar9 = *(uint *)(param_4 + 8);
      uVar4 = uVar9;
      if ((int)uVar9 < iVar6) {
        do {
          lVar2 = *(longlong *)(param_2 + 0x20);
          iVar6 = *(int *)(param_2 + 0x14);
          uVar7 = (ulonglong)(uint)(iVar6 * iVar8);
          lVar5 = (ulonglong)uVar9 * 2;
          uVar1 = *(ushort *)(lVar5 + uVar7 + lVar2);
          local_58 = (int)*(short *)((ulonglong)(uint)((iVar8 + -1) * iVar6) + lVar5 + lVar2);
          local_54 = (int)*(short *)(uVar7 + (ulonglong)(uVar9 - 1) * 2 + lVar2);
          local_50 = (uint)*(short *)(uVar7 + (ulonglong)(uVar4 + 1) * 2 + lVar2);
          local_4c = (int)*(short *)((ulonglong)(uint)(iVar3 * iVar6) + lVar5 + lVar2);
          FUN_140384850(&local_58,local_48,4,0);
          if (local_54 <= (short)uVar1) {
            local_54._0_2_ = uVar1;
          }
          if ((int)(short)uVar1 <= (int)local_50) {
            local_50 = (uint)(ushort)local_54;
          }
          uVar9 = uVar9 + 1;
          *(short *)((ulonglong)(uint)(iVar8 * *(int *)(local_60 + 0x14)) + lVar5 +
                    *(longlong *)(local_60 + 0x20)) = (short)local_50;
          iVar6 = *(int *)(param_4 + 0x10);
          param_2 = local_68;
          uVar4 = uVar4 + 1;
        } while ((int)uVar9 < iVar6);
      }
      iVar8 = iVar8 + 1;
    } while (iVar8 < *(int *)(param_4 + 0x14));
  }
  return 0;
}



// ===== depth0 FUN_1403857e0 @ 0x1403857e0 rva=0x3857e0 size=780 =====

undefined8
FUN_1403857e0(undefined8 param_1,longlong param_2,longlong param_3,longlong param_4,int param_5,
             int param_6,int param_7,longlong param_8)

{
  longlong lVar1;
  undefined2 uVar2;
  int iVar3;
  longlong lVar4;
  longlong lVar5;
  longlong lVar6;
  int iVar7;
  uint uVar8;
  int iVar9;
  int iVar10;
  int iVar11;
  int iVar12;
  ulonglong uVar13;
  int iVar14;
  int iVar15;
  int iVar16;
  ulonglong uVar17;
  int iVar18;
  int iVar19;
  ulonglong uVar20;
  int iVar21;
  uint uVar22;
  uint local_68;
  
  iVar15 = *(int *)(param_4 + 0xc);
  if (iVar15 < *(int *)(param_4 + 0x14)) {
    iVar11 = *(int *)(param_4 + 0x10);
    iVar3 = iVar15;
    do {
      iVar3 = iVar3 + 1;
      uVar22 = *(uint *)(param_4 + 8);
      if ((int)uVar22 < iVar11) {
        local_68 = uVar22 + 1;
        do {
          lVar1 = *(longlong *)(param_2 + 0x20);
          iVar11 = *(int *)(param_2 + 0x14);
          uVar13 = (ulonglong)(uint)(iVar11 * iVar15);
          uVar20 = (ulonglong)(uint)(iVar11 * iVar3);
          uVar17 = (ulonglong)(uint)((iVar15 + -1) * iVar11);
          lVar4 = (ulonglong)uVar22 * 2;
          iVar21 = (int)*(short *)(lVar1 + lVar4 + uVar13);
          lVar5 = (ulonglong)(uVar22 - 1) * 2;
          lVar6 = (ulonglong)local_68 * 2;
          iVar18 = (((((((iVar21 * 6 - (int)*(short *)(uVar20 + lVar1 + lVar4)) -
                        (int)*(short *)(lVar6 + lVar1 + uVar13)) -
                       (int)*(short *)(uVar17 + lVar1 + lVar4)) -
                      (int)*(short *)(lVar1 + lVar5 + uVar13)) * 2 -
                     (int)*(short *)(lVar1 + uVar20 + lVar6)) -
                    (int)*(short *)(uVar20 + lVar1 + lVar5)) -
                   (int)*(short *)(uVar17 + lVar6 + lVar1)) -
                   (int)*(short *)(uVar17 + lVar1 + lVar5);
          iVar19 = -2;
          iVar7 = (int)(iVar18 + (iVar18 >> 0x1f & 0xfU)) >> 4;
          iVar21 = param_5 + (iVar21 - iVar7);
          iVar18 = 0;
          if (-1 < iVar21) {
            iVar18 = iVar21;
          }
          if (0x7fff < iVar21) {
            iVar18 = 0x7fff;
          }
          iVar16 = 0;
          iVar10 = 0;
          iVar21 = *(int *)(param_8 + (longlong)iVar18 * 4);
          local_68 = local_68 + 1;
          do {
            uVar8 = (iVar19 + iVar15) * iVar11;
            uVar13 = (ulonglong)uVar8;
            iVar14 = (int)*(short *)(lVar1 + (ulonglong)(uVar22 - 2) * 2 + (ulonglong)uVar8);
            iVar12 = iVar14 - iVar18;
            iVar9 = -iVar12;
            if (iVar9 < 0) {
              iVar9 = iVar12;
            }
            if ((iVar9 < iVar21) && (iVar19 != 0)) {
              iVar16 = iVar16 + iVar14;
              iVar10 = iVar10 + 1;
            }
            iVar14 = (int)*(short *)(uVar13 + lVar5 + lVar1);
            iVar12 = iVar14 - iVar18;
            iVar9 = -iVar12;
            if (iVar9 < 0) {
              iVar9 = iVar12;
            }
            if ((iVar9 < iVar21) && (iVar19 != 0)) {
              iVar16 = iVar16 + iVar14;
              iVar10 = iVar10 + 1;
            }
            iVar14 = (int)*(short *)(lVar6 + uVar13 + lVar1);
            iVar12 = iVar14 - iVar18;
            iVar9 = -iVar12;
            if (iVar9 < 0) {
              iVar9 = iVar12;
            }
            if ((iVar9 < iVar21) && (iVar19 != 0)) {
              iVar16 = iVar16 + iVar14;
              iVar10 = iVar10 + 1;
            }
            iVar14 = (int)*(short *)((ulonglong)local_68 * 2 + uVar13 + lVar1);
            iVar12 = iVar14 - iVar18;
            iVar9 = -iVar12;
            if (iVar9 < 0) {
              iVar9 = iVar12;
            }
            if ((iVar9 < iVar21) && (iVar19 != 0)) {
              iVar16 = iVar16 + iVar14;
              iVar10 = iVar10 + 1;
            }
            iVar19 = iVar19 + 1;
          } while (iVar19 < 3);
          iVar7 = iVar7 * param_6 >> 8;
          iVar11 = -param_7;
          if (-param_7 <= iVar7) {
            iVar11 = iVar7;
          }
          if (param_7 < iVar7) {
            iVar11 = param_7;
          }
          iVar18 = (iVar18 + iVar16) / (iVar10 + 1) + (iVar11 - param_5);
          iVar11 = 0;
          if (-1 < iVar18) {
            iVar11 = iVar18;
          }
          uVar2 = (short)iVar11;
          if (0x3fff < iVar18) {
            uVar2 = 0x3fff;
          }
          uVar22 = uVar22 + 1;
          *(undefined2 *)
           ((ulonglong)(uint)(iVar15 * *(int *)(param_3 + 0x14)) + lVar4 +
           *(longlong *)(param_3 + 0x20)) = uVar2;
          iVar11 = *(int *)(param_4 + 0x10);
        } while ((int)uVar22 < iVar11);
      }
      iVar15 = iVar15 + 1;
    } while (iVar15 < *(int *)(param_4 + 0x14));
  }
  return 0;
}



// ===== depth0 FUN_14039d230 @ 0x14039d230 rva=0x39d230 size=865 =====

/* WARNING: Function: __security_check_cookie replaced with injection: security_check_cookie */

undefined8 FUN_14039d230(undefined8 param_1,longlong param_2,longlong *param_3)

{
  undefined4 uVar1;
  undefined4 uVar2;
  longlong lVar3;
  int iVar4;
  longlong lVar5;
  undefined8 *puVar6;
  longlong lVar7;
  longlong lVar8;
  undefined8 uVar9;
  undefined1 auStack_168 [32];
  undefined ***local_148;
  undefined4 local_140;
  undefined **local_138;
  undefined4 local_130;
  undefined4 local_12c;
  undefined4 local_128;
  undefined4 local_124;
  undefined8 local_120;
  undefined8 uStack_118;
  undefined8 local_110;
  undefined8 uStack_108;
  undefined8 local_100;
  undefined8 uStack_f8;
  undefined8 local_f0;
  undefined8 local_e8;
  undefined8 uStack_e0;
  undefined8 local_d8;
  undefined8 uStack_d0;
  undefined8 local_c8;
  undefined8 uStack_c0;
  undefined8 local_b8;
  undefined8 local_b0;
  undefined8 uStack_a8;
  undefined8 local_a0;
  undefined8 uStack_98;
  undefined8 local_90;
  undefined8 uStack_88;
  undefined8 local_80;
  undefined8 local_78;
  undefined8 uStack_70;
  undefined8 local_68;
  undefined8 uStack_60;
  undefined8 local_58;
  undefined8 uStack_50;
  undefined8 local_48;
  ulonglong local_40;
  
  local_40 = DAT_140559440 ^ (ulonglong)auStack_168;
  if ((((param_2 == 0) || (*(longlong *)(param_2 + 8) == 0)) || (param_3 == (longlong *)0x0)) ||
     (*param_3 == 0)) {
    uVar9 = 6;
  }
  else {
    local_138 = sony_zhacai::ZcRectT<int>::vftable;
    local_130 = *(undefined4 *)(param_2 + 0x30);
    local_12c = *(undefined4 *)(param_2 + 0x34);
    local_128 = *(undefined4 *)(param_2 + 0x38);
    local_124 = *(undefined4 *)(param_2 + 0x3c);
    lVar5 = FUN_140152710(*(longlong *)(param_2 + 8),0);
    if (lVar5 != 0) {
      lVar3 = param_3[1];
      uVar1 = *(undefined4 *)(lVar3 + 0x22c);
      uVar2 = *(undefined4 *)(lVar3 + 0x234);
      lVar8 = 0;
      lVar7 = 0;
      if (*(int *)(lVar3 + 0x230) == 2) {
        local_120 = 0;
        uStack_118 = 0;
        local_110 = 0;
        uStack_108 = 0;
        local_100 = 0;
        uStack_f8 = 0;
        local_f0 = 0;
        FUN_140152c00(&local_120);
        local_b0 = 0;
        uStack_a8 = 0;
        local_a0 = 0;
        uStack_98 = 0;
        local_90 = 0;
        uStack_88 = 0;
        local_80 = 0;
        FUN_140152c00(&local_b0);
        iVar4 = FUN_140152cb0(&local_120,*(undefined4 *)(lVar5 + 8),*(undefined4 *)(lVar5 + 0xc));
        if ((iVar4 == 0) &&
           (iVar4 = FUN_140152cb0(&local_b0,*(undefined4 *)(lVar5 + 8),*(undefined4 *)(lVar5 + 0xc))
           , lVar7 = lVar8, iVar4 == 0)) {
          (*(code *)local_138[9])(&local_138,2);
          FUN_1401ce980(lVar5,&local_120,&local_138);
          (*(code *)local_138[9])(&local_138,1);
          puVar6 = (undefined8 *)FUN_14039d740(param_1,&local_120,&local_138,uVar2);
          if (puVar6 != (undefined8 *)0x0) {
            local_148 = &local_138;
            local_140 = uVar1;
            lVar7 = FUN_14039d5a0(param_1,lVar5,&local_120,puVar6);
            if (lVar7 != 0) {
              (*(code *)local_138[9])(&local_138,5);
            }
            (**(code **)*puVar6)(puVar6,1);
          }
        }
        FUN_140152c90(&local_b0);
        puVar6 = &local_120;
      }
      else {
        local_e8 = 0;
        uStack_e0 = 0;
        local_d8 = 0;
        uStack_d0 = 0;
        local_c8 = 0;
        uStack_c0 = 0;
        local_b8 = 0;
        FUN_140152c00(&local_e8);
        local_78 = 0;
        uStack_70 = 0;
        local_68 = 0;
        uStack_60 = 0;
        local_58 = 0;
        uStack_50 = 0;
        local_48 = 0;
        FUN_140152c00(&local_78);
        iVar4 = FUN_140152cb0(&local_e8,*(undefined4 *)(lVar5 + 8),*(undefined4 *)(lVar5 + 0xc));
        if ((iVar4 == 0) &&
           (iVar4 = FUN_140152cb0(&local_78,*(undefined4 *)(lVar5 + 8),*(undefined4 *)(lVar5 + 0xc))
           , lVar7 = lVar8, iVar4 == 0)) {
          (*(code *)local_138[9])(&local_138,1);
          FUN_1401ce7f0(lVar5,&local_e8,&local_138);
          (*(code *)local_138[9])(&local_138,1);
          puVar6 = (undefined8 *)FUN_14039d740(param_1,&local_e8,&local_138,uVar2);
          if (puVar6 != (undefined8 *)0x0) {
            local_148 = &local_138;
            local_140 = uVar1;
            lVar7 = FUN_14039d5a0(param_1,lVar5,&local_e8,puVar6);
            if (lVar7 != 0) {
              (*(code *)local_138[9])(&local_138,6);
            }
            (**(code **)*puVar6)(puVar6,1);
          }
        }
        FUN_140152c90(&local_78);
        puVar6 = &local_e8;
      }
      FUN_140152c90(puVar6);
      if (lVar7 != 0) {
        FUN_140152730(*(undefined8 *)(param_2 + 8),0,lVar7);
        *(undefined4 *)(param_2 + 0x30) = local_130;
        *(undefined4 *)(param_2 + 0x38) = local_128;
        *(undefined4 *)(param_2 + 0x34) = local_12c;
        *(undefined4 *)(param_2 + 0x3c) = local_124;
        return 0;
      }
    }
    uVar9 = 1;
  }
  return uVar9;
}



// ===== depth0 FUN_14039d050 @ 0x14039d050 rva=0x39d050 size=359 =====

/* WARNING: Function: __security_check_cookie replaced with injection: security_check_cookie */

undefined8 FUN_14039d050(undefined8 param_1,longlong param_2,longlong *param_3)

{
  longlong lVar1;
  undefined8 uVar2;
  uint uVar3;
  undefined1 auStack_78 [32];
  longlong local_58;
  undefined8 local_50;
  undefined4 local_48;
  undefined4 local_40;
  float local_38;
  undefined ***local_30;
  undefined **local_28;
  undefined8 local_20;
  undefined8 uStack_18;
  ulonglong local_10;
  
  local_10 = DAT_140559440 ^ (ulonglong)auStack_78;
  lVar1 = *(longlong *)(param_2 + 0x68);
  local_28 = sony_zhacai::ZcRectT<int>::vftable;
  local_20 = 0;
  uStack_18 = 0;
  uVar3 = 0;
  if ((*(int *)(*param_3 + 0x144) == 3) && (uVar3 = 0, *(int *)(param_3[1] + 0x21c) < -0x32)) {
    uVar3 = (-0x32 - *(int *)(param_3[1] + 0x21c)) * 2;
  }
  uVar2 = FUN_140152710(*(undefined8 *)(param_2 + 8),0);
  local_48 = *(undefined4 *)(lVar1 + 0xc00ec);
  local_40 = *(undefined4 *)(lVar1 + 0xc00f8);
  local_20 = *(undefined8 *)(param_2 + 0x30);
  uStack_18 = *(undefined8 *)(param_2 + 0x38);
  local_38 = (float)uVar3 / DAT_1404df210;
  local_58 = lVar1 + 0x800d0;
  local_30 = &local_28;
  local_50 = 0x8000;
  FUN_14039cb30(uVar2,0,lVar1 + 0x200d0,0x8000);
  local_20._0_4_ = *(undefined4 *)(param_2 + 0x30);
  uStack_18._0_4_ = *(undefined4 *)(param_2 + 0x38);
  local_20._4_4_ = *(undefined4 *)(param_2 + 0x34);
  uStack_18._4_4_ = *(undefined4 *)(param_2 + 0x3c);
  local_58 = CONCAT44(local_58._4_4_,6);
  (*(code *)local_28[0xb])(&local_28,6,6,6);
  *(undefined4 *)(param_2 + 0x30) = (undefined4)local_20;
  *(undefined4 *)(param_2 + 0x38) = (undefined4)uStack_18;
  *(undefined4 *)(param_2 + 0x34) = local_20._4_4_;
  *(undefined4 *)(param_2 + 0x3c) = uStack_18._4_4_;
  return 0;
}



